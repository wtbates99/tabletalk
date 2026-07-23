"""Machine-readable report formats for CI and run history."""

from __future__ import annotations

import json
from xml.etree import ElementTree

from tabletalk.evals.models import SuiteResult


def json_report(result: SuiteResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True)


def junit_report(result: SuiteResult) -> str:
    """Serialize each eval case as a JUnit testcase."""
    duration = 0.0
    if result.cases:
        duration = sum(case.trace.latency_ms for case in result.cases) / 1000
    root = ElementTree.Element(
        "testsuite",
        {
            "name": result.suite_name,
            "tests": str(len(result.cases)),
            "failures": str(result.failed_count),
            "time": f"{duration:.3f}",
        },
    )
    properties = ElementTree.SubElement(root, "properties")
    ElementTree.SubElement(
        properties,
        "property",
        {"name": "aggregate_score", "value": f"{result.score:.6f}"},
    )
    ElementTree.SubElement(
        properties,
        "property",
        {"name": "run_id", "value": result.run_id},
    )

    for case in result.cases:
        test_case = ElementTree.SubElement(
            root,
            "testcase",
            {
                "classname": result.suite_name,
                "name": case.case_name,
                "time": f"{case.trace.latency_ms / 1000:.3f}",
            },
        )
        if not case.passed:
            failed_metrics = [metric for metric in case.metrics if not metric.passed]
            failure = ElementTree.SubElement(
                test_case,
                "failure",
                {
                    "message": ", ".join(metric.name for metric in failed_metrics),
                    "type": "TableTalkEvalFailure",
                },
            )
            failure.text = json.dumps(
                {metric.name: metric.details for metric in failed_metrics},
                indent=2,
                default=str,
            )
        output = ElementTree.SubElement(test_case, "system-out")
        output.text = json.dumps(case.to_dict(), indent=2, default=str)

    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True)

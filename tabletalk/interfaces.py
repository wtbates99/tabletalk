import json
import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

import yaml

logger = logging.getLogger("tabletalk")


# ── Abstract interfaces ────────────────────────────────────────────────────────

class DatabaseProvider(ABC):
    @abstractmethod
    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """Execute SQL and return results as a list of row dicts."""
        pass

    @abstractmethod
    def get_client(self) -> Any:
        """Return the underlying database client/connection."""
        pass

    @abstractmethod
    def get_database_type_map(self) -> Dict[str, str]:
        """Return a mapping of native type names to compact codes."""
        pass

    @abstractmethod
    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch table schemas in compact format.

        Returns:
            List of dicts, each with:
              't': full table name (e.g. 'schema.table')
              'd': table description
              'f': list of field dicts:
                    'n': column name
                    't': compact type code
                    'pk': True if primary key (optional)
                    'fk': 'other_table.col' if foreign key (optional)
        """
        pass


class LLMProvider(ABC):
    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Single-turn: generate a complete response for a prompt."""
        pass

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        """Single-turn streaming. Default yields the full response as one chunk."""
        yield self.generate_response(prompt)

    def generate_chat_stream(
        self, messages: List[Dict[str, str]]
    ) -> Generator[str, None, None]:
        """
        Multi-turn streaming with a full messages list
        (OpenAI format: [{'role': 'system'|'user'|'assistant', 'content': '...'}]).
        Default falls back to single-turn with the last user message.
        """
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        yield from self.generate_response_stream(last_user)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _encode_field(f: Dict[str, Any]) -> str:
    """Encode a field dict to compact notation like 'id:I[PK]' or 'user_id:I[FK:users.id]'."""
    col = f"{f['n']}:{f['t']}"
    annotations = []
    if f.get("pk"):
        annotations.append("PK")
    if f.get("fk"):
        annotations.append(f"FK:{f['fk']}")
    if annotations:
        col += f"[{','.join(annotations)}]"
    return col


def _format_results_for_llm(results: List[Dict[str, Any]], limit: int = 15) -> str:
    """Format query results as a compact text table for LLM consumption."""
    if not results:
        return "(empty)"
    cols = list(results[0].keys())
    rows = results[:limit]
    header = " | ".join(cols)
    sep = "-+-".join("-" * max(len(c), 8) for c in cols)
    lines = [header, sep]
    for row in rows:
        lines.append(" | ".join(str(row.get(c, ""))[:30] for c in cols))
    if len(results) > limit:
        lines.append(f"... ({len(results) - limit} more rows)")
    return "\n".join(lines)


# ── QuerySession ──────────────────────────────────────────────────────────────

class QuerySession:
    _SYSTEM_PROMPT = (
        "You are a SQL expert and data analyst helping users explore their database "
        "through natural language. You are a dbt companion — you understand schema "
        "relationships and write clean, production-quality SQL.\n\n"
        "Rules:\n"
        "- Return ONLY the SQL query — no markdown, no code fences, no explanations\n"
        "- Use the correct SQL dialect shown in DATA_SOURCE\n"
        "- Columns marked [PK] are primary keys — use them for JOINs\n"
        "- Columns marked [FK:table.col] define JOIN relationships — use them exactly\n"
        "- For follow-up questions, build on the previous query in the conversation\n"
        "- Write clean SQL with proper aliases and consistent formatting\n\n"
        "Database Schema:\n{schema}"
    )

    _EXPLAIN_PROMPT = (
        'The user asked: "{question}"\n\n'
        "This SQL query ran:\n{sql}\n\n"
        "Results ({n} row{plural}):\n{preview}\n\n"
        "In 1-2 sentences of plain English, explain what this data shows. "
        "Highlight key numbers and insights. Do not mention SQL, column names, or code."
    )

    _SUGGEST_PROMPT = (
        "Given this database schema:\n{schema}\n\n"
        "{context}"
        "Suggest 3 specific, interesting questions a data analyst might ask. "
        "Return ONLY a JSON array of 3 strings, nothing else.\n"
        'Example: ["Top 10 customers by revenue this month?", '
        '"Average order value by product category?", '
        '"How many new users signed up last week?"]'
    )

    _FIX_PROMPT = (
        "This SQL query failed:\n{sql}\n\n"
        "Error message:\n{error}\n\n"
        "Database schema:\n{schema}\n\n"
        "Return ONLY the corrected SQL. No explanation, no code fences."
    )

    def __init__(self, project_folder: str):
        self.project_folder = project_folder
        self.config = self._load_config()
        self.llm_provider = self._get_llm_provider()
        self._db_provider: Optional[DatabaseProvider] = None
        self._db_loaded = False

    # ── Config & provider init ─────────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        config_path = os.path.join(self.project_folder, "tabletalk.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(config_path) as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise ValueError(f"Invalid config format in {config_path}")
        return config

    def _get_llm_provider(self) -> LLMProvider:
        from tabletalk.factories import get_llm_provider

        llm_config = self.config.get("llm", {})
        if not llm_config or "provider" not in llm_config or "api_key" not in llm_config:
            raise ValueError("LLM configuration missing or incomplete in tabletalk.yaml")
        try:
            return get_llm_provider(llm_config)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize LLM provider: {e}")

    def get_db_provider(self) -> Optional[DatabaseProvider]:
        """Lazily initialize the database provider for query execution."""
        if self._db_loaded:
            return self._db_provider
        self._db_loaded = True
        from tabletalk.factories import get_db_provider

        # Support both 'profile' reference and inline 'provider' block
        if "profile" in self.config:
            provider_config: Dict[str, Any] = {"profile": self.config["profile"]}
        else:
            provider_config = self.config.get("provider", {})

        if not provider_config:
            return None
        try:
            self._db_provider = get_db_provider(provider_config)
            return self._db_provider
        except Exception as e:
            logger.warning(f"Could not initialize DB provider for execution: {e}")
            return None

    # ── Manifest ───────────────────────────────────────────────────────────────

    def load_manifest(self, manifest_file: str) -> str:
        path = os.path.join(self.project_folder, "manifest", manifest_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Manifest not found: {path}")
        with open(path) as f:
            return f.read()

    # ── SQL generation ─────────────────────────────────────────────────────────

    @staticmethod
    def _clean_sql(sql: str) -> str:
        """Strip markdown code fences from LLM output."""
        sql = re.sub(r"```(?:sql)?\n?", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```", "", sql)
        return sql.strip()

    def _build_messages(
        self,
        schema: str,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        """Build the full messages list for a chat-style LLM call."""
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._SYSTEM_PROMPT.format(schema=schema)}
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": question})
        return messages

    def generate_sql(self, manifest_data: str, question: str) -> str:
        """Single-turn SQL generation (non-streaming)."""
        messages = self._build_messages(manifest_data, question)
        try:
            chunks = list(self.llm_provider.generate_chat_stream(messages))
            return self._clean_sql("".join(chunks))
        except Exception as e:
            raise RuntimeError(f"Error generating SQL: {e}")

    def generate_sql_stream(
        self, manifest_data: str, question: str
    ) -> Generator[str, None, None]:
        """Single-turn SQL streaming (no conversation context)."""
        yield from self.generate_sql_conversational(manifest_data, question, [])

    def generate_sql_conversational(
        self,
        manifest_data: str,
        question: str,
        history: List[Dict[str, str]],
    ) -> Generator[str, None, None]:
        """Multi-turn SQL streaming with full conversation context."""
        messages = self._build_messages(manifest_data, question, history)
        try:
            for chunk in self.llm_provider.generate_chat_stream(messages):
                yield chunk
        except Exception as e:
            raise RuntimeError(f"Error generating SQL: {e}")

    # ── Execution ──────────────────────────────────────────────────────────────

    def execute_sql(self, sql: str) -> List[Dict[str, Any]]:
        db = self.get_db_provider()
        if db is None:
            raise RuntimeError("No database provider configured for execution.")
        return db.execute_query(sql)

    # ── Explanation ───────────────────────────────────────────────────────────

    def explain_results_stream(
        self,
        question: str,
        sql: str,
        results: List[Dict[str, Any]],
    ) -> Generator[str, None, None]:
        """Stream a plain-English explanation of query results."""
        n = len(results)
        prompt = self._EXPLAIN_PROMPT.format(
            question=question,
            sql=sql,
            n=n,
            plural="s" if n != 1 else "",
            preview=_format_results_for_llm(results),
        )
        yield from self.llm_provider.generate_response_stream(prompt)

    # ── Fix ────────────────────────────────────────────────────────────────────

    def fix_sql_stream(
        self, sql: str, error: str, manifest_data: str
    ) -> Generator[str, None, None]:
        """Stream a corrected SQL query given an error message."""
        prompt = self._FIX_PROMPT.format(sql=sql, error=error, schema=manifest_data)
        yield from self.llm_provider.generate_response_stream(prompt)

    # ── Suggestions ───────────────────────────────────────────────────────────

    def suggest_questions(
        self,
        manifest_data: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        """Return up to 3 suggested questions for the current schema + context."""
        context = ""
        if history:
            recent = history[-4:]
            lines = [f"{m['role']}: {m['content'][:120]}" for m in recent]
            context = "Recent conversation:\n" + "\n".join(lines) + "\n\n"
        prompt = self._SUGGEST_PROMPT.format(schema=manifest_data, context=context)
        try:
            response = self.llm_provider.generate_response(prompt)
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                return [str(q) for q in parsed[:3]]
            return []
        except Exception as e:
            logger.debug(f"suggest_questions failed: {e}")
            return []

    # ── History ───────────────────────────────────────────────────────────────

    def save_history(self, manifest: str, question: str, sql: str) -> None:
        path = os.path.join(self.project_folder, ".tabletalk_history.jsonl")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest,
            "question": question,
            "sql": sql,
        }
        try:
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Could not save history: {e}")

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        path = os.path.join(self.project_folder, ".tabletalk_history.jsonl")
        if not os.path.exists(path):
            return []
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries[-limit:]

    # ── Favorites ─────────────────────────────────────────────────────────────

    def _favorites_path(self) -> str:
        return os.path.join(self.project_folder, ".tabletalk_favorites.json")

    def get_favorites(self) -> List[Dict[str, Any]]:
        path = self._favorites_path()
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return json.load(f)

    def save_favorite(
        self, name: str, manifest: str, question: str, sql: str
    ) -> None:
        favorites = [f for f in self.get_favorites() if f.get("name") != name]
        favorites.append(
            {
                "name": name,
                "manifest": manifest,
                "question": question,
                "sql": sql,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        with open(self._favorites_path(), "w") as f:
            json.dump(favorites, f, indent=2)

    def delete_favorite(self, name: str) -> bool:
        favorites = self.get_favorites()
        new = [f for f in favorites if f.get("name") != name]
        if len(new) == len(favorites):
            return False
        with open(self._favorites_path(), "w") as f:
            json.dump(new, f, indent=2)
        return True


# ── Parser ─────────────────────────────────────────────────────────────────────

class Parser:
    def __init__(self, project_folder: str, db_provider: DatabaseProvider):
        self.project_folder = project_folder
        self.db_provider = db_provider

    def apply_schema(self) -> None:
        config_path = os.path.join(self.project_folder, "tabletalk.yaml")
        try:
            with open(config_path) as f:
                defaults = yaml.safe_load(f)
            if not isinstance(defaults, dict):
                raise ValueError("Invalid tabletalk.yaml format.")
            for key in ("provider", "contexts", "output"):
                if key not in defaults:
                    raise ValueError(f"Missing key '{key}' in tabletalk.yaml")
            data_source_desc = defaults.get("description", "")
            provider_type = defaults["provider"].get("type", "unknown")
            data_source_line = f"DATA_SOURCE: {provider_type} - {data_source_desc}"
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            return

        contexts_folder = os.path.join(self.project_folder, defaults["contexts"])
        output_folder = os.path.join(self.project_folder, defaults["output"])
        os.makedirs(output_folder, exist_ok=True)

        for context_file in sorted(os.listdir(contexts_folder)):
            if not context_file.endswith(".yaml"):
                continue
            context_path = os.path.join(contexts_folder, context_file)
            try:
                with open(context_path) as f:
                    context_config = yaml.safe_load(f)
                if not isinstance(context_config, dict):
                    logger.warning(f"Invalid format in '{context_file}', skipping.")
                    continue
            except Exception as e:
                logger.error(f"Error reading '{context_file}': {e}")
                continue

            context_name = context_config.get("name", "unnamed_context")
            context_desc = context_config.get("description", "")
            version = context_config.get("version", "1.0")
            context_line = f"CONTEXT: {context_name} - {context_desc} (v{version})"
            output_lines = [data_source_line, context_line]

            schema_list = context_config.get("datasets") or context_config.get("schemas", [])
            for schema_item in schema_list:
                schema_name = schema_item.get("name")
                schema_desc = schema_item.get("description", "")
                if not schema_name:
                    logger.warning(f"Missing schema name in '{context_file}', skipping item.")
                    continue
                output_lines.append(f"DATASET: {schema_name} - {schema_desc}")
                output_lines.append("TABLES:")

                tables = schema_item.get("tables", [])
                yaml_table_desc: Dict[str, Optional[str]] = {}
                table_names: List[str] = []
                for table in tables:
                    if isinstance(table, str):
                        yaml_table_desc[f"{schema_name}.{table}"] = None
                        table_names.append(table)
                    elif isinstance(table, dict):
                        full = f"{schema_name}.{table['name']}"
                        yaml_table_desc[full] = table.get("description", "")
                        table_names.append(table["name"])
                    else:
                        logger.warning(f"Invalid table entry in '{schema_name}'.")

                try:
                    compact_tables = self.db_provider.get_compact_tables(
                        schema_name, table_names
                    )
                    for ct in compact_tables:
                        tname = ct["t"]
                        yaml_desc = yaml_table_desc.get(tname)
                        desc = yaml_desc if yaml_desc is not None else ct.get("d", "")
                        fields_str = "|".join(_encode_field(f) for f in ct["f"])
                        output_lines.append(f"{tname}|{desc}|{fields_str}")
                except Exception as e:
                    logger.error(f"Error fetching tables for '{schema_name}': {e}")
                    continue

            output_file = os.path.join(output_folder, context_file.replace(".yaml", ".txt"))
            try:
                with open(output_file, "w") as f:
                    f.write("\n".join(output_lines))
                logger.info(f"Generated manifest for '{context_file}'")
            except Exception as e:
                logger.error(f"Error writing '{output_file}': {e}")

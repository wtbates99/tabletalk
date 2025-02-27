# Main configuration file that calls modules
module "example_module" {
  path = "./modules/example"
  config = {
    project_id = "${var.PROJECT_ID}"
    region     = "${var.REGION}"
  }
}

module "sales_data" {
  path = "./modules/sales"
  config = {
    project_id = "${var.PROJECT_ID}"
    region     = "${var.REGION}"
  }
}

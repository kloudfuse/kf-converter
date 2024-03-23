# kf-converter

This repository contains the binaries to run the conversions of artifacts from source query languages (Datadog, Wavefront, etc) to Grafana/PromQL.

To use the tools in this repo, please follow the steps below.

* Install python 3.10: brew install python3
* mkdir /tmp/kfuse-parser
- If running python3.10 (this may not be upgraded in future updates)
* unzip kfuse_parser-0.0.1-py3.10.egg -d /tmp/kfuse-parser
- if running python3.12 (recommended)

* unzip kfuse_parser-0.0.1-py3.12.egg -d /tmp/kfuse-parser
* pip3 install -r /tmp/kfuse-parser/requirements.txt
* Convert dashboards:
   - Run to see help: `./dashboard_converter -h`
   - Convert a single dashboard: `./dashboard_converter -f <new_sample_db.json> -c <sample_settings.yaml>`
* Convert alerts
   - Run to see help: `./alert_converter -h`
   - Convert a single alert: `./alert_converter -f <new_sample_alert.json> -c <sample_settings.yaml>`

# conversions-binary

This repository contains the binaries to run the conversions of artifacts from source query languages (Datadog, Wavefront, etc) to PromQL.

To use the tools in this repo, please follow the steps below.

1. mkdir /tmp/kfuse-parser
2. unzip kfuse_parser-0.0.1-py3.12.egg -d /tmp/kfuse-parser
3. run the dashboard converter:
   Run to see help:
   ./dashboard_converter -h
   Run to convert a single dashboard
   ./dashboard_converter -f <new_sample_db.json> -c <sample_settings.yaml>
4. run the alert converter:
   ./alert_converter -h
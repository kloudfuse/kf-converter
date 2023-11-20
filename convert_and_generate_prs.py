#!/usr/bin/python3
# This script automates the steps to generate PR. The script takes following steps:
# 1. Processes the csv file containing the dashboard links to retrieve 'namespaces', dashboard names and links, reviewers.
# 2. Create namespace directories
# 3. download dashboards
# 4. Conversion
# 5. generate PRs.

import argparse
import csv
import subprocess
import os
import sys
import traceback
from pprint import PrettyPrinter
import logging
import glob
import json
pp = PrettyPrinter()

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
fileHandler = logging.FileHandler('automate_output.log', mode='a')
streamHandler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
streamHandler.setFormatter(formatter)
fileHandler.setFormatter(formatter)
logger.addHandler(streamHandler)
logger.addHandler(fileHandler)
logger.setLevel(logging.DEBUG)

invalidReviewers = {}
invalidReviewer = 0
invalidArtifactFiles = {}
g_args = None
toplevel_dir = os.path.join(os.getcwd())
logger.info(toplevel_dir)
artifact_download_cmd_str = "curl '%s' -H 'Authorization: Bearer %s' | jq > \"%s\""
converter_abs_dir = os.path.join(os.path.abspath(toplevel_dir), os.path.pardir, 'conversions-binary')
logger.info(converter_abs_dir)

db_pr_body = '''This PR contains dashboard artifacts generated for migration from Wavefront to Pharos.
Steps to follow:
1.  Optional: browse to URL in wavefront_dashboard_link_dbname.txt to see original dashboard.
2.  Optional: upload dbname_wf_prom.json to wavefront and review PromQL queries there.
3.  Search for “CHANGE_ME” in dbname_grafana_summary.json. Review suggested solutions in https://docs.google.com/document/d/10TAnV-ynug2seMk2L6TNFcprRdRRshHZAn9Dk4KzUb0/edit#.
4.  Search for "CHANGE_ME" in dbname_grafana_wrapped.json. If found, review suggested steps in https://docs.google.com/document/d/10TAnV-ynug2seMk2L6TNFcprRdRRshHZAn9Dk4KzUb0/edit#.
5.  Import dashboard into Pharos via Web UI, API or pipeline. See https://pharos.inday.io/docs/dashboards/grafana/.
6.  Reach out to us with questions or issues.
7.  When happy, add a comment to this PR to indicate you are complete. There is no need to merge this PR. Nor is there any need to update this PR with changes you have made to the dashboard or alert.
When you are finished with this PR, you can go to https://pharos.inday.io/docs/metrics/ for dashboards or https://pharos.inday.io/docs/alerting/metrics/ for alerts for documentation on how to maintain the dashboard or alert going forward.
Files in this PR:
• [Optional] dbname_dashboard_wrapped_grafana.json - Converted dashboard appropriate for importing to Pharos via API.
• dbname_grafana.json - Converted dashboard appropriate for importing to Pharos via UI.
• dbname_orig.json - Original Wavefront dashboard.
• dbname_summary.json - Summary of conversion including failure descriptions.
• dbname_wf_prom.json - Wavefront dashboard with WavefrontQL queries replaced with PromQL queries. Where query failed to convert, both original and new query are present.
• wavefront_dashboard_link_dbname.txt - Link to the original Wavefront dashboard.'''

alert_pr_body = '''This PR contains alert artifacts generated for migration from Wavefront to Pharos.
Steps to follow:
1.  Search for “CHANGE_ME” in alertid_pharos_report.json. Review suggested solutions in https://docs.google.com/document/d/10TAnV-ynug2seMk2L6TNFcprRdRRshHZAn9Dk4KzUb0/edit#.
2.  Optional: Compare and contrast with the original alert (report file, i.e., alertid_pharos_report.json, contains the link to the original alert).
3.  Search for "CHANGE_ME" in alertid_pharos.yaml. Review suggested solutions in https://docs.google.com/document/d/10TAnV-ynug2seMk2L6TNFcprRdRRshHZAn9Dk4KzUb0/edit#.
4.  Import alert into Pharos via API or pipeline. See https://pharos.inday.io/docs/alerting/metrics/.
5.  Reach out to us with questions or issues.
6.  When happy, add a comment to this PR to indicate you are complete. There is no need to merge this PR. Nor is there any need to update this PR with changes you have made to the dashboard or alert.
When you are finished with this PR, you can go to https://pharos.inday.io/docs/metrics/ for dashboards or https://pharos.inday.io/docs/alerting/metrics/ for alerts for documentation on how to maintain the dashboard or alert going forward.
Files in this PR:
• alertid_pharos.yaml - Converted alert appropriate for importing to Pharos via API or pipeline.
• alertid_orig.json - Original Wavefront alert.
• alertid_pharos_report.json - Report of conversion including failure descriptions.'''

def run(cmd, dryrun=False, check=True, shell=True, capture_output=False, timeout=None, *args):
    assert not(dryrun and capture_output) or dryrun ^ capture_output,\
        "Only one of dryrun/capture_output should be set; fix the code"
    logger.info("{}".format(' '.join(cmd)))
    if dryrun:
        return
    try:
        return subprocess.run(cmd, check=check, shell=shell, timeout=timeout,
                              capture_output=capture_output, *args)
    except subprocess.TimeoutExpired:
        if timeout is not None:
            logger.info("\"{}\" timed out".format(' '.join(cmd)))
            pass

def process_input_names():
    '''
    Processes list of artifact ids and extracts db_links, db_names, namspaces and reviewers for each of db link.
    '''
    dblinks = []
    dbnames = []
    namespaces = []
    reviewers = []
    service_teams = []
    logger.info("processing names %s", g_args.input_names)
    i = 0
    for artifact_name in g_args.input_names:
        dblink = g_args.artifact_download_url % (g_args.artifact_type, artifact_name)
        reviewer = g_args.additional_reviewers[0] if len(g_args.additional_reviewers) == 1 else ','.join(g_args.additional_reviewers)
        namespace = g_args.use_namespace
        if reviewer == "" or reviewer is None:
            logger.info("skipping db %s at line %d for empty reviewer" % (dblink, i))
            continue
        logger.info("will convert %s, %s, %s", dblink, namespace, reviewer)
        dbname = dblink.split('/')[-1].strip()
        dblinks.append(dblink)
        dbnames.append(dbname)
        namespaces.append(namespace)
        reviewers.append(reviewer)
        service_teams.append('%s-service-team-name' % namespace)
    return dblinks, dbnames, namespaces, reviewers, service_teams

def process_csv(filename=None):
    '''
    Processes a given csv and extracts db_links, db_names, namspaces and reviewers for each of db link.
    - ignores if line doesn't have a link for db
    - ignores if reviewer is empty
    - ignores if dashboard isn't marked critical.
    '''
    assert filename is not None
    dblinks = []
    dbnames = []
    namespaces = []
    reviewers = []
    service_teams = []
    regenerate_list = []
    regenerate = False
    skip_checks = g_args.skip_checks
    if g_args.use_regenerate_list:
        with open('regenerate.list') as regenerate_file:
            for line in regenerate_file.readlines():
                if line.startswith('#') or line.strip() == '':
                    continue
                regenerate = True
                regenerate_list.append(line.strip())
    logger.info("processing file %s", filename)
    logger.info("will regenerate these artifacts %s", regenerate_list)
    if g_args.artifact_type == "dashboard":
        dblink_idx=5
        to_be_converted_idx=8
        skip_generation_idx = 10
        namespace_idx=11
        reviewer_idx=12
        service_team_idx = 0 # N/A for dashboards
        not_yet_created_idx = 17
    else:
        assert g_args.artifact_type == "alert"
        dblink_idx=8
        to_be_converted_idx=11
        skip_generation_idx = 13
        namespace_idx=14
        reviewer_idx=16
        service_team_idx = 15
        not_yet_created_idx = 22
    num_rows_invalid = 0
    num_rows_reviewer_empty = 0
    num_rows_namespace_service_empty = 0
    num_rows_skipped_as_marked = 0
    num_rows_skipped_regenerate = 0
    num_rows_skipped_not_new = 0
    with open(filename, newline='') as csvfile:
        csvreader = csv.reader(csvfile)
        i = -1
        num_not_created = 0
        for row in csvreader:
            i += 1
            logger.debug("checking line: %d: %s", i, row)
            dblink = row[dblink_idx].strip()
            if not dblink.startswith('https'):
                logger.info('ignoring %s', row)
                num_rows_invalid += 1
                continue
            only_convert_new = False
            if not_yet_created_idx is not None:
                only_convert_new = row[not_yet_created_idx].strip().lower()
            if only_convert_new == "not created":
                num_not_created += 1
            reviewer = row[reviewer_idx].strip()
            # if reviewer == "":
            #     logger.info('skipping db %s at line %d for empty reviewer, row: %s (not-converted: %s)', dblink, i, row, only_convert_new)
            #     num_rows_reviewer_empty += 1
            #     continue
            to_be_converted = row[to_be_converted_idx]
            if not skip_checks and to_be_converted != 'TRUE':
                logger.info('skipping db %s at line %d as it is not to be converted, row: %s (not-converted: %s)', dblink, i, row, only_convert_new)
                continue
            namespace = row[namespace_idx].strip()
            service_team = row[service_team_idx].strip()
            if namespace == "" or (g_args.artifact_type == "alert" and service_team == ""):
                logger.info('skipping db  %s at line %d because namespace/service team is not listed, row: %s (not-converted: %s)', dblink, i, row, only_convert_new)
                num_rows_namespace_service_empty += 1
                continue
            skip_generation = row[skip_generation_idx]
            dbname = dblink.split('/')[-1].strip()
            if regenerate and dbname not in regenerate_list:
                logger.info('skipping db %s at line %d because it is not in regenerate list, row: %s (not-converted: %s)', dblink, i, row, only_convert_new)
                num_rows_skipped_regenerate += 1
                continue
            elif not regenerate and (not skip_checks and skip_generation == 'TRUE'):
                logger.info('skipping db %s at line %d because skip generation is true, row: %s (not-converted: %s)', dblink, i, row, only_convert_new)
                num_rows_skipped_as_marked += 1
                continue
            if g_args.only_convert_new and only_convert_new != "not created":
                logger.info('skipping db %s at line %d because it is already created earlier, row: %s (not-converted: %s)', dblink, i, row, only_convert_new)
                num_rows_skipped_not_new += 1
                continue
            logger.info("will convert %s, %s, %s, %s, %s, %s", dblink, to_be_converted, namespace, service_team, dbname, reviewer)
            dblinks.append(dblink)
            dbnames.append(dbname)
            namespaces.append(namespace)
            service_teams.append(service_team)
            reviewers.append(reviewer)
    logger.info("got %d artifacts to process out of %d processed lines (num_not_created: %d)", len(dbnames), i, num_not_created)
    logger.info("num_rows_invalid: %d", num_rows_invalid)
    logger.info("num_rows_reviewer_empty: %d", num_rows_reviewer_empty)
    logger.info("num_rows_namespace_service_empty: %d", num_rows_namespace_service_empty)
    logger.info("num_rows_skipped_as_marked: %d", num_rows_skipped_as_marked)
    logger.info("num_rows_skipped_regenerate: %d", num_rows_skipped_regenerate)
    logger.info("num_rows_skipped_not_new: %d", num_rows_skipped_not_new)
    if regenerate and len(regenerate_list) != len(dbnames):
        will_not_process = set(regenerate_list) - set(dbnames)
        logger.info("following (%d) artifacts will not be processed: %s", len(will_not_process), will_not_process)
    assert len(dblinks) == len(dbnames)
    assert len(dblinks) == len(namespaces)
    assert len(dblinks) == len(reviewers)
    assert len(dblinks) == len(service_teams)
    return dblinks, dbnames, namespaces, reviewers, service_teams

def get_artifact_folder(folder_prefix='wf-'):
    return '%s%ss' % (folder_prefix, g_args.artifact_type)

def get_dirname(namespace, service_team, folder_prefix='wf-'):
    if g_args.artifact_type == "dashboard":
        return os.path.join(toplevel_dir, namespace, get_artifact_folder(folder_prefix=folder_prefix))
    elif g_args.artifact_type == "alert":
        if service_team != "":
            return os.path.join(toplevel_dir, namespace, get_artifact_folder(folder_prefix=folder_prefix), service_team)
        else:
            return os.path.join(toplevel_dir, namespace, get_artifact_folder(folder_prefix=folder_prefix))
    else:
        assert False

def mkdirs(namespaces, service_teams):
    '''
    Creates the directories based on namespaces. A directory is created for each namespace and 'wf-dashboards'
    directory is created inside.
    '''
    dirnames = []
    for i in range(len(namespaces)):
        ns = namespaces[i]
        service_team = service_teams[i]
        dirname = get_dirname(ns, service_team)
        logger.info('creating dir %s', dirname)
        os.makedirs(dirname, exist_ok=True)
        dirnames.append(dirname)
    assert len(dirnames) == len(namespaces)
    return dirnames

def download_artifacts(artifact_type, dbnames, dirnames, namespaces, service_teams, force=False, dryrun=False):
    '''
    download the db in the link and store that in it's corresponding namespace directory.
    '''
    def _check_exists_and_valid(dbname):
        dbfilenames = ['%s.json' % dbname, '%s_orig.json' % dbname]
        for db_filename in dbfilenames:
            if os.path.exists(db_filename) and os.path.getsize(db_filename) != 0:
                import json
                invalid = False
                with open(db_filename) as dbf:
                    contents = json.loads(dbf.read())
                    if artifact_type == "alert":
                        if "status" in contents and contents["status"]["code"] != 200:
                            invalid = True
                if invalid:
                    logger.debug("exist but invalid (file: %s, exists: %s, size: %s)", db_filename, os.path.exists(db_filename), os.path.getsize(db_filename))
                    continue
                logger.debug("exist and valid (file: %s, exists: %s, size: %s)", db_filename, os.path.exists(db_filename), os.path.getsize(db_filename))
                return True
        logger.debug("does not exist or invalid (file: %s)", dbfilenames)
        return False
    def _copy_if_needed(dbname):
        orig_file = '%s_orig.json' % dbname
        base_file = '%s.json' % dbname
        logger.info("will copy as needed, working_dir: %s, orig_file: %s (exist: %s), base_file: %s (exist: %s)", os.getcwd(), orig_file, os.path.exists(orig_file), base_file, os.path.exists(base_file))
        if os.path.exists(orig_file) and not os.path.exists(base_file):
            run(['cp %s %s' % (orig_file, base_file)])
        assert os.path.exists(base_file)
    num_downloaded = 0
    start_from = g_args.start_from
    end_at = g_args.end_at
    for i in range(len(dbnames)):
        logger.info("downloading artifact: dirname: %s, namespace: %s, service_team: %s, dbname: %s", dirnames[i], namespaces[i], service_teams[i], dbnames[i])
        if start_from is not None:
            if dbnames[i] != start_from:
                logger.debug("skipping download of %s", dbnames[i])
                continue
            else:
                start_from = None
        try:
            logger.info("changing dir %s (for %s)", dirnames[i], dbnames[i])
            os.chdir(dirnames[i])
        except FileNotFoundError:
            ns = namespaces[i]
            service_team = service_teams[i]
            dirname = get_dirname(ns, service_team)
            logger.info('creating dir %s', dirname)
            os.makedirs(dirnames[i], exist_ok=True)
        branchname = get_branchname(namespaces[i], service_teams[i], dbnames[i])
        failed = checkout_branch(branchname, dryrun=dryrun)
        if failed:
            logger.debug("failed to check out branch: %s", branchname)
            checkout_branch("main", existing=True)
            continue
        logger.info("current working dir: %s (dirname: %s)", os.getcwd(), dirnames[i])
        assert os.getcwd() == dirnames[i]
        db_filename = '%s.json' % dbnames[i]
        logger.info("checking if %s already exists: %s (force:%s)", db_filename, os.path.exists(db_filename), force)
        if force or not _check_exists_and_valid(dbnames[i]):
            logger.info('downloading %s to %s (force: %s, exists: %s, size: %s',
                        dbnames[i], db_filename, force, os.path.exists(db_filename), os.path.getsize(db_filename) if os.path.exists(db_filename) else 'NA')
            download_cmd = artifact_download_cmd_str % (g_args.artifact_download_url %(artifact_type, dbnames[i]), g_args.bearer, db_filename)
            r = run([download_cmd], dryrun=dryrun)
            if r.returncode != 0:
                logger.error("failed to download (cmd: %s)", download_cmd)
            num_downloaded += 1
        else:
            logger.info('not downloading %s', db_filename)
            _copy_if_needed(dbnames[i])
        if not _check_exists_and_valid(dbnames[i]):
            logger.error("found zero size or invalid artifact file: %s", os.path.join(dirnames[i], db_filename))
            global invalidArtifactFiles
            invalidArtifactFiles.update({dbnames[i]:os.path.join(dirnames[i], db_filename)})
        checkout_branch("main", existing=True)
        if end_at is not None and dbnames[i] == end_at:
            break
    os.chdir(toplevel_dir)
    logger.debug("downloaded %d dashboards", num_downloaded)

def check_if_conversion_needed(dirname=None, dbname=None, force_convert=False, dryrun=False):
    # converted files don't exist, so convert.
    if g_args.artifact_type == "dashboard":
        outfile = os.path.join(dirname, '%s_grafana.json' % dbname)
        exists = os.path.exists(outfile)
        logger.debug("checking if file %s exist: %s", outfile, exists)
        if exists:
            if not force_convert:
                logger.info("no conversion needed for %s as converted file exist", outfile)
                return False
            logger.info("converted files (%s) exist, but force convert is true", outfile)
        logger.info("conversion needed for %s", dbname)
        return True
    if g_args.artifact_type == "alert":
        base_file = os.path.join(dirname, '%s.yaml' % dbname)
        orig_file = os.path.join(dirname, '%s_orig.yaml' % dbname)
        files = glob.glob(os.path.join(dirname, '%s*_pharos.yaml' % dbname))
        logger.debug("checking if converted files %s exist: %s, force: %s", files, len(files), force_convert)
        if len(files) > 0:
            if not force_convert:
                logger.info("no conversion needed for %s as converted files (%s) exist", dbname, files)
                return False
            logger.info("converted files (%s) exist, but force_convert is on", files)
        if os.path.exists(orig_file) and not os.path.exists(base_file):
            logger.debug("copying base file %s, copying from: %s", base_file, orig_file)
            cp_cmd = 'cp %s %s' % (orig_file, base_file)
            run([cp_cmd], dryrun=dryrun)
        logger.info("conversion needed for %s (force: %s)", dbname, force_convert)
        return True
    assert False

def rename_if_necesasry(dirname=None, dbname=None, dryrun=False):
    if g_args.artifact_type == 'dashboard':
        oldfile = os.path.join(dirname, '%s_prom.json' % dbname)
        if os.path.exists(oldfile):
            newfile = os.path.join(dirname, '%s_wf_prom.json' % dbname)
            try:
                mv_cmd = 'git mv "%s" "%s"' % (oldfile, newfile)
                run([mv_cmd], dryrun=dryrun)
            except:
                cp_cmd = 'cp "%s" "%s"' % (oldfile, newfile)
                run([cp_cmd], dryrun=dryrun)
        oldfile = os.path.join(dirname, '%s.json' % dbname)
        if os.path.exists(oldfile):
            newfile = os.path.join(dirname, '%s_orig.json' % dbname)
            try:
                mv_cmd = 'git mv "%s" "%s"' % (oldfile, newfile)
                run([mv_cmd], dryrun=dryrun)
            except:
                cp_cmd = 'cp "%s" "%s"' % (oldfile, newfile)
                run([cp_cmd], dryrun=dryrun)
    elif g_args.artifact_type == 'alert':
        oldfiles = [os.path.join(dirname, '%s.json' % dbname),
                    os.path.join(dirname, '%s_cortex.yaml' % dbname),
                    os.path.join(dirname, '%s_cortex_report.json' % dbname)]
        newfiles = [os.path.join(dirname, '%s_orig.json' % dbname),
                    os.path.join(dirname, '%s_pharos.yaml' % dbname),
                    os.path.join(dirname, '%s_pharos_report.json' % dbname)]
        for i in range(len(oldfiles)):
            oldfile = oldfiles[i]
            newfile = newfiles[i]
            if os.path.exists(oldfile):
                try:
                    mv_cmd = 'git mv "%s" "%s"' % (oldfile, newfile)
                    run([mv_cmd], dryrun=dryrun)
                except:
                    mv_cmd = 'mv "%s" "%s"' % (oldfile, newfile)
                    run([mv_cmd], dryrun=dryrun)
    else:
        assert False

def create_dblink_file(dirname=None, dbname=None, dblink=None, dryrun=False):
    if g_args.artifact_type == 'alert':
        logger.info("no link file for alerts")
        return
    # Create link file.
    link_file = os.path.join(dirname, 'wavefront_dashboard_link_%s.txt' % dbname)
    if os.path.exists(link_file):
        return
    logger.info("writing db link (%s) to file %s", dblink, link_file)
    write_linkfile_cmd = 'echo "%s" >> "%s"' % (dblink, link_file)
    run([write_linkfile_cmd], dryrun=dryrun)

def get_pr_link(pr_num):
    pr_link = 'https://ghe.megaleo.com/wavefront-migration/dashboards/pull/%s' % pr_num
    if g_args.artifact_type == 'alert':
        pr_link = 'https://ghe.megaleo.com/wavefront-migration/alerts/pull/%s' % pr_num
    return pr_link

def get_generate_pr_command(reviewers, branchname):
    generate_pr_cmd = 'gh pr create %s -f --head %s --base main -b "%s"' % (reviewers, branchname, db_pr_body)
    if g_args.artifact_type == 'alert':
        generate_pr_cmd = 'gh pr create %s -f --head %s --base main -b "%s"' % (reviewers, branchname, alert_pr_body)
    return generate_pr_cmd

def create_pr(reviewers, branchname, additional_reviewers, dbname, dryrun=False):
    try:
        generate_pr_cmd = get_generate_pr_command(reviewers, branchname)
        out = run([generate_pr_cmd], dryrun=dryrun, capture_output=True)
        output = out.stdout.decode('utf-8').strip().split('\t')
        return output
    except subprocess.CalledProcessError as cpe:
        # XXX: failed to create PR doesn't necessarily mean it's due to an invalid reviewer
        stderrstr = cpe.stderr.decode('utf-8').strip()
        if stderrstr.find("Could not resolve to a User") != -1 and additional_reviewers is not None:
            global invalidReviewer
            invalidReviewer += 1
            global     invalidReviewers
            invalidReviewers[reviewers] = dbname
            reviewers_arg = ' '.join(['-r %s' % tr for tr in additional_reviewers])
            logger.info("found invalidReviewer '%s' for %s but retrying with additional reviewers: %s", reviewers, dbname, reviewers_arg)
            return create_pr(reviewers_arg, branchname, None, dbname, dryrun)
        else:
            logger.info("failed to create pr for %s:%s (output: %s, stderr: %s, output: %s)", g_args.artifact_type, dbname, cpe.stdout, cpe.stderr, cpe.output)
            raise cpe

def create_pr_if_needed(reviewers, branchname, additional_reviewers, msg, namespace, service_team, dbname, commited, dryrun=False):
    # Check if there's already a PR on this branch. (by default this command shows only open prs)
    check_pr_cmd = 'gh pr list --head %s --base main' % branchname
    out = run([check_pr_cmd], capture_output=True)
    pr_link = None
    pr_num = None
    if out.returncode == 0 and len(out.stdout) != 0:
        output = out.stdout.decode('utf-8').strip().split('\t')
        pr_num = output[0]
        pr_status = output[-1]
        pr_link = get_pr_link(pr_num)
        logger.info('a pr on branch %s already exists and is %s: %s', branchname, pr_status, pr_link)
    else:
        check_pr_cmd = 'gh pr list --head %s --base main -s closed' % branchname
        if out.returncode == 0 and len(out.stdout) != 0:
            pr_link = get_pr_link(pr_num)
            logger.info('a pr on branch %s already exists and is closed. will not generate a PR. %s', branchname, pr_status, pr_link)
            return pr_link, False
        output = create_pr(reviewers, branchname, additional_reviewers, dbname, dryrun)
        pr_link = output[0]
        pr_num = pr_link.split('/')[-1]
    # Add the additional reviewers:
    if additional_reviewers is not None and pr_num is not None:
        reviewers=','.join(additional_reviewers)
        body = db_pr_body
        if g_args.artifact_type == 'alert':
            body = alert_pr_body
        title = get_git_commit_msg('%s update' % msg, namespace, service_team, dbname)
        logger.info("adding reviewers %s to PR %s", reviewers, pr_num)
        pr_edit_cmd='gh pr edit %s --remove-reviewer chris-leege --add-reviewer %s --title "%s" --body "%s"' % (pr_num, reviewers, title, body)
        run([pr_edit_cmd], capture_output=True)
    return pr_link, True

def get_filenames(dirname, artifact_name):
    if g_args.artifact_type == "dashboard":
        filenames = [
            os.path.join(dirname, '%s_orig.json' % artifact_name),
            os.path.join(dirname, '%s_wf_prom.json' % artifact_name),
            os.path.join(dirname, '%s_grafana.json' % artifact_name),
            os.path.join(dirname, '%s_summary.json' % artifact_name),
            os.path.join(dirname, 'wavefront_dashboard_link_%s.txt' % artifact_name),
            ]
        # This file doesn't get generated always. If not there, then add contents containing instructions.
        if not os.path.exists(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%artifact_name)):
            wrapped_content = {
                "dashboard": json.load(open(os.path.join(dirname, '%s_grafana.json' % artifact_name), 'r')),
                "FolderID": 'CHANGE_ME_FOLDER_ID',
                "Overwrite": True
            }
            with open(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%artifact_name), 'w') as wrapped_file:
                json.dump(wrapped_content, wrapped_file)
        try:
            filenames.append(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%artifact_name))
        except FileNotFoundError as fne:
            logger.error("ignoring not found file: %s" % (os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%artifact_name)))
        return filenames
    else:
        assert g_args.artifact_type == "alert"
        files = glob.glob(os.path.join(dirname, '%s_pharos.yaml' % artifact_name)) + glob.glob(os.path.join(dirname, '%s_pharos_report.json' % artifact_name))
        return [os.path.join(dirname, '%s_orig.json' % artifact_name)] + files

def get_converter_cmd():
    targetinfo = '' if g_args.artifact_type == 'dashboard' else '-t %s/notificants.json' % converter_abs_dir
    return "%s/%s_converter -c %s/conversion_settings.yaml %s" % (converter_abs_dir, g_args.artifact_type, converter_abs_dir, targetinfo)

def get_git_commit_msg(msg, namespace, sevice_team, dbname):
    if g_args.artifact_type == "dashboard":
        return '%s: %s %s for %s' % (msg, g_args.artifact_type, dbname, namespace)
    else:
        return '%s: %s %s for %s/%s' % (msg, g_args.artifact_type, dbname, namespace, sevice_team)

def get_git_commit_cmd(msg, namespace, sevice_team, dbname):
    return 'git commit -a -m "%s"' % get_git_commit_msg(msg, namespace, sevice_team, dbname)

def are_files_changed():
    result = run(['git status -u no'], capture_output=True)
    changed = True
    output = result.stdout.decode('utf-8').strip()
    if output.find('nothing to commit') != -1:
        changed = False
    logger.info("has any files changed? %s. output: %s", "YES" if changed else "NO", output)
    return changed

def git_mv_wrong_files(filenames, namespace, service_team, dirname, artifact_name):
    moved = False
    #
    # If due to layout changes etc. we have to rename files etc. do it here.
    #

    # We accidentally created files (and generated PRs) with wrong folder name once.
    # i.e., created directory with name 'wf_<artifact_type>s' rather than wf-<artifact_type>s
    # The following code block before attempts to fix that.
    # Following case is rectified completely, shouldn't happen anymore. So comment
    # wrong_folder_name = get_artifact_folder(folder_prefix='wf_')
    # correct_folder_name = get_artifact_folder()
    # for f in filenames:
    #     # If we have created wrong file, then this would be the name of the file.
    #     wrong_filename = f.replace(correct_folder_name, wrong_folder_name)
    #     if os.path.exists(wrong_filename):
    #         logger.debug('renaming "%s" -> "%s"', wrong_filename, f)
    #         try:
    #             git_mv_cmd = 'git mv -f "%s" "%s"' % (wrong_filename, f)
    #             run([git_mv_cmd])
    #         except:
    #             mv_cmd = 'mv -f "%s" "%s"' % (wrong_filename, f)
    #             run([mv_cmd])
    #         moved = True
    # if g_args.artifact_type == "alert":
    #     # # deal with case when alert files got created in wf_alerts.
    #     #
    #     # wrong_dirname = get_dirname(namespace, service_team, folder_prefix='wf_')
    #     # logger.info("looking for old files in %s", wrong_dirname)
    #     # old_files = list(set(glob.glob(os.path.join(wrong_dirname, '%s*_cortex.yaml' % artifact_name)) + glob.glob(os.path.join(wrong_dirname, '%s*_cortex_report.json' % artifact_name))) -
    #     #             set(glob.glob(os.path.join(wrong_dirname, '%s_cortex.yaml' % artifact_name)) + glob.glob(os.path.join(wrong_dirname, '%s_cortex_report.json' % artifact_name))))
    #     #
    #     # # deal with the case where serive_name folder wasn't created.
    #     wrong_dirname = get_dirname(namespace, "")
    #     logger.info("looking for old files in %s", wrong_dirname)
    #     old_files = glob.glob(os.path.join(wrong_dirname, '%s_cortex.yaml' % artifact_name)) + glob.glob(os.path.join(wrong_dirname, '%s_cortex_report.json' % artifact_name))
    #     logger.info("will be moving %s files", old_files)
    #     for f in old_files:
    #         if os.path.exists(f):
    #             try:
    #                 git_rm_cmd = 'git rm -f %s' % f
    #                 run(([git_rm_cmd]))
    #             except:
    #                 rm_cmd = 'rm -f %s' % f
    #                 run(([rm_cmd]))
    return moved

def git_add_and_commit(filenames, msg, namespace, service_team, dbname, dirname, branchname, dryrun=False):
    git_mv_wrong_files(filenames, namespace, service_team, dirname, dbname)
    git_add_cmd = 'git add %s' % ' '.join(['"%s"' % fn for fn in filenames])
    run([git_add_cmd], dryrun=dryrun)
    # if not are_files_changed():
    #     return False
    git_commit_cmd = get_git_commit_cmd(msg, namespace, service_team, dbname)
    try:
        run([git_commit_cmd], capture_output=True if dryrun == False else False, dryrun=dryrun)
    except subprocess.CalledProcessError as cpe:
        logger.info("failed to commit (output: %s, stderr: %s)", cpe.stdout, cpe.stderr)
        logger.info("Continuing on git commit error")
    git_pull_rebase_cmd = 'git pull --rebase origin main'
    run([git_pull_rebase_cmd], dryrun=dryrun)
    create_remote_branch_cmd = 'git push -f origin %s' % branchname
    run([create_remote_branch_cmd], dryrun=dryrun)
    return True

def checkout_branch(branchname, existing=False, dryrun=False):
    failed = False
    try:
        if existing:
            raise subprocess.CalledProcessError(returncode=-1, cmd="just want to checkout", output="just want to checkout")
        co_cmd = 'git checkout -b %s' % branchname
        run([co_cmd], dryrun=dryrun)
    except subprocess.CalledProcessError as cpe:
        try:
            co_cmd = 'git checkout -f %s' % branchname
            run([co_cmd], dryrun=dryrun)
        except:
            failed = True
    return failed

def get_branchname(namespace, service_team, dbname):
    if g_args.artifact_type == "dashboard":
        branchname = '%s_%ss' % (dbname, g_args.artifact_type)
    else:
        if service_team != "":
            branchname = '%s_%s_%s_%ss' % (namespace, service_team, dbname, g_args.artifact_type)
        else:
            branchname = '%s_%s_%ss' % (namespace, dbname, g_args.artifact_type)
    if ' ' in branchname:
        branchname = branchname.replace(' ', '_')
    return branchname

def create_prs(dblinks=[],
               dirnames=[],
               reviewers=[],
               namespaces=[],
               service_teams=[],
               dbnames=[],
               msg=None,
               dryrun=False,
               start_from=None,
               end_at=None,
               convert=True,
               stop_on_n=0,
               force_convert=False,
               test=False,
               test_reviewers=None,
               additional_reviewers=None):
    '''
    Creates PR by following these steps.
    For each db in list of dashboards:
        1. create/checkout branch with the name of <dbname>_dashboards
        2. Add all files ending with .json in that directory to the commit.
        3. rebases with current main (tot)
        4. pushes the created branch to create a remote branch.
        5. creates a PR based on diff of main and the remote branch (gh create PR)
    '''
    cwd = os.getcwd()
    os.chdir(toplevel_dir)
    validReviewers = {}
    no_converted_files = 0
    conversion_failures = {}
    prs = {}
    duplicate_artifacts = {}
    prs_closed = {}
    num_prs_attempted = 0
    num_processed = 0
    invalid_dashboard_names = {}
    if additional_reviewers is None:
        additional_reviewers = ["owen-sullivan", "vijayarajan-k"]
    else:
        additional_reviewers = additional_reviewers + ["owen-sullivan", "vijayarajan-k"]
    if test:
        if msg is None:
            msg = 'TEST - DO NOT REVIEW'
    logger.info("starting PR creation (start: %s, end: %s)", start_from, end_at)
    for i in range(len(dbnames)):
        dbname = dbnames[i]
        if start_from is not None:
            if dbname != start_from:
                logger.info("not processing %s", dbname)
                continue
            else:
                # We found the dbname to start off from, let's reset start_from now
                # so we process everything from now on.
                start_from = None
        num_processed += 1
        dblink = dblinks[i]
        dbfilename = '%s.json' % dbname
        dirname = dirnames[i]
        namespace = namespaces[i]
        service_team = service_teams[i]
        logger.info("%d: working with %s -- dirname: %s, name: %s, link: %s, namespace: %s, reviewers:%s", i, g_args.artifact_type, dirname, dbname, dblink, namespace, reviewers[i])
        if dbname in prs or dbname in prs_closed:
            if dbname not in duplicate_artifacts:
                duplicate_artifacts.update({dbname: dblink})
            continue
        branchname = get_branchname(namespace, service_team, dbname)
        logger.info("%d: checking out %s", i, branchname)
        failed = checkout_branch(branchname)
        if failed:
            logger.error("falied to create branch %s. Moving on..", branchname)
            invalid_dashboard_names.update({dbname:dblink})
            continue
        if dbname in invalidArtifactFiles:
            logger.error("Not procesing invalid artifact %s (%s). Moving on..", dbname, invalidArtifactFiles[dbname])
            continue
        # Rename if necessary.
        # git_mv_wrong_files(get_filenames(dirname, dbname), namespace, service_team, dirname, dbname)
        honor_force_convert = True and force_convert
        if force_convert and g_args.start_converting_from is not None and num_processed <= g_args.start_converting_from:
            honor_force_convert = False
        should_convert = check_if_conversion_needed(dirname=dirname, dbname=dbname, force_convert=honor_force_convert)
        if (convert and should_convert):
            logger.info("%d: %s converting %ss in %s (current dir:%s)", i, "force" if force_convert else "", g_args.artifact_type, dirname, os.getcwd())
            converter_full_cmd = '%s -f "%s"' % (get_converter_cmd(), os.path.join(dirname, dbfilename))
            try:
                run([converter_full_cmd], dryrun=dryrun)
            except subprocess.CalledProcessError as cpe:
                logger.error("failed to convert %s (cmd: %s)", dbname, converter_full_cmd)
                try: # Running it again to capture output.
                    run([converter_full_cmd], dryrun=dryrun, capture_output=True)
                except subprocess.CalledProcessError as cpe:
                    logger.error("failure: stderr: %s, stdout: %s: out: %s", cpe.stderr, cpe.stdout, cpe.output)
                conversion_failures.update({dbname:traceback.format_exc()})
                continue
        if g_args.skip_pr_unconditionally:
            logger.info("%d: skipping pr update for %s unconditionally", i, dbname)
            prs.update({dbname: "placeholder"})
            continue
        if not should_convert and g_args.skip_pr_if_no_change:
            prs.update({dbname: "placeholder"})
            logger.info("%d: no updates needed for %s", i, dbname)
            continue
        # This renames the files if we already converted them.
        rename_if_necesasry(dirname=dirname, dbname=dbname, dryrun=dryrun)
        # This creates a file with link to db in it.
        create_dblink_file(dirname=dirname, dbname=dbname, dblink=dblink, dryrun=dryrun)
        filenames = get_filenames(dirname, dbname)
        commited = git_add_and_commit(filenames, msg, namespace, service_team, dbname, dirname, branchname, dryrun=dryrun)
        if test:
            reviewers_arg = ' '.join(['-r %s' % tr for tr in test_reviewers])
        else:
            reviewers_arg = ' '.join(['-r %s' % r.strip() for r in reviewers[i].split(',')])
        # Only create PR if needed, otherwise push is enough.
        try:
            pr_link, created = create_pr_if_needed(reviewers_arg, branchname, additional_reviewers, msg, namespace, service_team, dbname, commited, dryrun=dryrun)
            if pr_link is not None:
                if created:
                    prs.update({dbname: pr_link})
                else:
                    prs_closed.update({dbname: pr_link})
        except subprocess.CalledProcessError as cpe:
            logger.info("failed to create pr for %s:%s (output: %s, stderr: %s, output: %s)", g_args.artifact_type, dbname, cpe.stdout, cpe.stderr, cpe.output)
            # XXX: failed to create PR doesn't necessarily mean it's due to an invalid reviewer
            if cpe.stderr is not None:
                stderrstr = cpe.stderr.decode('utf-8').strip()
                if stderrstr.find("Could not resolve to a User") != -1:
                    logger.info("invalid ghe reviewer (%s) for %s", cpe, dbname)
                    global invalidReviewer
                    invalidReviewer += 1
                    global     invalidReviewers
                    invalidReviewers[reviewers[i]] = dbname
                    logger.info("invalidReviewer %s for %s", reviewers[i], dbname)
            else:
                validReviewers[reviewers[i]] = True
        logger.info("%d: validReviewers: %s", i, validReviewers.keys())
        num_prs_attempted += 1
        co_main_cmd = 'git checkout main'
        run([co_main_cmd], dryrun=dryrun)
        if end_at is not None and dbname == end_at:
            logger.info("stopping at given artifact")
            break
        if stop_on_n != 0 and num_prs_attempted >= stop_on_n:
            break
    checkout_branch("main", existing=True)
    logger.info("number of invalid reviewers (non-unique): %s", invalidReviewer)
    logger.info("number of branches with no converted files: %s", no_converted_files)
    logger.info("number of artifacts processed: %d", num_processed)
    logger.info("number of PRs attempted: %d", num_prs_attempted if not dryrun else 0)
    logger.info("number of PRs created: %d", len(prs))
    logger.info("number of PRs closed and skipped: %d", len(prs_closed))
    logger.info("number of duplicate artifacts: %s", len(duplicate_artifacts))
    logger.info("encoutered following duplicate artifacts: %s", pp.pformat(duplicate_artifacts))
    logger.info("encountered %d empty db files", len(invalidArtifactFiles))
    logger.info("encountered %d conversion failures", len(conversion_failures))
    logger.info("encountered %d invalid db names", len(invalid_dashboard_names))
    logger.info("encountered following empty artifact files: %s", pp.pformat(invalidArtifactFiles))
    logger.info("encountered following failures: %s", pp.pformat(conversion_failures))
    logger.info("encountered following invalid db names: %s", pp.pformat(invalid_dashboard_names))
    logger.info("encountered following invalid reviewers (unique): %s", pp.pformat(invalidReviewers))
    logger.info("following PRs are created/exists: %s", pp.pformat(prs))
    logger.info("following PRs are closed and skipped %s", pp.pformat(prs_closed))
    os.chdir(cwd)


def exec_steps(args):
    if args.input_names is not None:
        logger.info("processing input names")
        dblinks, dbnames, namespaces, reviewers, service_teams = process_input_names()
    else:
        logger.info("processing input file")
        dblinks, dbnames, namespaces, reviewers, service_teams = process_csv(filename=args.input_file)
    logger.info("making directories")
    dirnames = mkdirs(namespaces, service_teams)
    logger.info("downloading artifacts")
    download_artifacts(args.artifact_type, dbnames, dirnames, namespaces, service_teams, dryrun=False, force=args.force_download)
    # download_dbs(dbnames, dirnames, dryrun=False, force=False)
    logger.info('number of artifacts downloaded:%s', len(dbnames))
    create_prs(dblinks=dblinks,
               dirnames=dirnames,
               reviewers=reviewers,
               namespaces=namespaces,
               service_teams=service_teams,
               dbnames=dbnames,
               dryrun=args.dryrun,
               start_from=args.start_from,
               end_at=args.end_at,
               msg=args.message,
               convert=(not args.skip_convert),
               force_convert = args.force_convert,
               stop_on_n=args.stop_on_n,
               test=args.test,
               test_reviewers=args.test_reviewers,
               additional_reviewers=args.additional_reviewers)

def main():
    parser = argparse.ArgumentParser(description="automate dashboard/alert download, conversion and PR generation")
    parser.add_argument('--artifact_download_url', default="https://current-provider/<artifact_type>/<artifact_name>", type=str, help="")
    parser.add_argument('--artifact_type', default=None, type=str, choices=['dashboard', 'alert'], help="whether working with alerts or dashboards")
    parser.add_argument('--bearer', default=None, type=str, help="bearer token value <api-key> to be used for curl requests")
    parser.add_argument('--input_names', default=None, action="append", help="list of artifact ids - exclusive with input_file")
    parser.add_argument('--use_namespace', default=None, help="provide the service_team name. Use test if testing. Required when using --input-names")
    parser.add_argument('--input_file', default='artifacts.csv', help="file containing the artifact details. exclusive with input_names")
    parser.add_argument('--start_from', default=None, help="start from this artifact name, ignoring everything before it")
    parser.add_argument('--end_at', default=None, help="stop at generating this artifact")
    parser.add_argument('--stop_on_n', default=0, type=int, help='stop after generating n artifacts')
    parser.add_argument('--skip_convert', default=False, action="store_true", help="skip conversion")
    parser.add_argument('--force_convert', default=False, action="store_true", help="force conversion")
    parser.add_argument('--test_reviewers', default=None, help='use this reviewer for test runs', action="append")
    parser.add_argument('--additional_reviewers', default=None, help='additional reviewers to include', action="append")
    parser.add_argument('--test', default=False, action='store_true', help='test automate process')
    parser.add_argument('--message', default="Conversion")
    parser.add_argument('--dryrun', default=False, action="store_true", help="dryrun mode, just prints the steps/commands")
    parser.add_argument('--use_regenerate_list', default=False, action="store_true", help="will regenerate PRs only in the 'regenerate.list' file.")
    parser.add_argument('--use_cancelled_list', default=False, action="store_true", help="will not update PRs for artificats in the 'cancelled.list' file.")
    parser.add_argument('--use_approved_list', default=False, action="store_true", help="will not update the PRs in the 'approved.list' file.")
    parser.add_argument('--only_convert_new', default=False, action="store_true", help="will only convert artifacts which are not converted yet")
    parser.add_argument('--force_download', default=False, action="store_true", help="redownload artifacts")
    parser.add_argument('--skip_pr_if_no_change', default=False, action="store_true", help="skip pr update if no change to converted file")
    parser.add_argument('--skip_pr_unconditionally', default=False, action="store_true", help="skip updating PRs unconditionally")
    parser.add_argument('--start_converting_from', default=None, type=int, help="start converting from this artifact #, until this # will force_convert doesn't have any effect")
    parser.add_argument('-skip_checks', default=False, action="store_true", help="skip checking for critical artifacts or marked to be converted etc fields and generate as long as namespace/service is known")
    args = parser.parse_args()
    logger.debug("running command: %s", ' '.join(sys.argv))
    logger.debug("args: %s", args)
    if args.test and args.test_reviewers is None:
        logger.info("--test_reviewer is required in test mode")
        parser.print_help()
        exit(-1)
    if args.artifact_type is None:
        logger.error("--artifact_type is required")
        parser.print_help()
        exit(-1)
    if not ((args.input_names is not None and args.use_namespace is not None) or args.input_file is not None):
        logger.error("check usage rules for --input_names/--input_file")
        parser.print_help()
        exit(-1)
    if args.input_names is not None and args.input_names[0].find(',') != -1:
        args.input_names = args.input_names[0].split(",")
        logger.debug("processing input_names: %s (type)", args.input_names)
    global g_args
    g_args = args
    exec_steps(args)

if __name__ == "__main__":
    # import pdb
    # pdb.runcall(main)
    main()
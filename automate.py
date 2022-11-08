#!/usr/bin/python3
# This script automates the steps to generate PR. The script takes following steps:
# 1. Processes the csv file containing the dashboard links to retrieve 'namespaces', dashboard names and links, reviewers.
# 2. Create namespace directories
# 3. download dashboards
# 4. TODO: Conversion
# 5. generate PRs.

import argparse
import csv
from sqlite3 import dbapi2
import subprocess
import os
import pdb
import sys
import traceback
from pprint import PrettyPrinter
import logging
import glob
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

g_args = None
toplevel_dir = os.path.join(os.getcwd())
logger.info(toplevel_dir)
artifact_url = "https://workday.wavefront.com/api/v2/%s/%s"
artifact_download_cmd_str = "curl '%s' -H 'Authorization: Bearer 537fdad9-f66a-4c79-ad81-f9c3faf1ee72' | jq > \"%s\""
converter_abs_dir = os.path.join(os.path.abspath(toplevel_dir), os.path.pardir, 'conversions-binary')
logger.info(converter_abs_dir)
db_pr_body = '''
This PR contains artifacts generated for migration from Wavefront to Pharos.
Contents:
- dbname_orig.json - contains the original wavefront dashboard file.
- dbname_wf_prom.json - contains the wavefront dashboard file with WQL queries replaced with PromQL queries.
- dbname_grafana.json - contains the converted dashboard to Grafana format. This format can be imported to Grafana from its UI.
- dbname_grafana_summary.json - contains the summary of the conversion with useful comments.
- dbname_dashboard_wrapped_grafana - contains the converted dashboard condusive to be uploaded to Grafana using its API.
- wavefront_dashboard_link_dbname.txt - contains the link to the original wavefront dashboard.
Steps to follow:
1. upload the dbname_wf_prom.json to wavefront, to review the query conversion. Each of the query converted sucessfully
   one should see the charts showing data. 
2. To compare and contrast, use the original wavefront dashboard link present in file wavefront_dashboard_link_dbname.txt.
3. Please check out the dbname_grafana_summary.json which summarizes the conversion. For those queries where data isn't showing in step 1, 
   (i.e., which failed conversion) look to see if why the query conversion failed.
   The summary file may sometime contain some workarounds which are to be manually applied, or some workarounds which require
   input dashboard to be changed first and then reconverted.
4. If the conversion looks ok, then approve the PR, otherwise based on the need to reconvert, the PR will be updated.
'''
alert_pr_body = '''
This PR contains artifacts generated for migration from Wavefront to Pharos.
Contents:
- alertid_orig.json - contains the original wavefront dashboard file.
- alertid[_severity]_cortex.yaml - contains the converted alert to Prometheus, alertmanager format. This format can be imported to prometheus using alerting api.
- alertid[_severity]_cortex_report.json - contains the report for the conversion.
'''

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
    logger.info("processing names %s", g_args.input_names)
    i = 0
    for artifact_name in g_args.input_names:
        dblink = artifact_url % (g_args.artifact_type, artifact_name)
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
    return dblinks, dbnames, namespaces, reviewers

def process_csv(filename=None, dblink_idx=5, reviewer_idx=10, namespace_idx=9, to_be_converted_idx=8):
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
    logger.info("processing file %s", filename)
    with open(filename, newline='') as csvfile:
        csvreader = csv.reader(csvfile)
        i = 0
        for row in csvreader:
            dblink = row[dblink_idx].strip()
            reviewer = row[reviewer_idx].strip()
            namespace = row[namespace_idx].strip()
            to_be_converted = row[to_be_converted_idx]
            if not dblink.startswith('https'):
                logger.info('ignoring %s', row)
                continue
            if reviewer == "":
                logger.info("skipping db %s at line %d for empty reviewer" % (dblink, i))
                continue
            if to_be_converted != 'TRUE':
                logger.info('skipping db %s at line %d as it is not to be converted', dblink, i)
                continue
            if namespace == "":
                logger.info('skipping db at line because namespace is not listed', dblink, i)
                continue
            logger.info("will convert %s, %s, %s, %s", dblink, to_be_converted, namespace, reviewer)
            dbname = dblink.split('/')[-1].strip()
            dblinks.append(dblink)
            dbnames.append(dbname)
            namespaces.append(namespace)
            reviewers.append(reviewer)
    return dblinks, dbnames, namespaces, reviewers

def mkdirs(namespaces):
    '''
    Creates the directories based on namespaces. A directory is created for each namespace and 'wf-dashboards'
    directory is created inside.
    '''
    dirnames = []
    for ns in namespaces:
        dirname = os.path.join(toplevel_dir, ns, 'wf_%ss' % g_args.artifact_type)
        logger.info('creating dir %s', dirname)
        os.makedirs(dirname, exist_ok=True)
        dirnames.append(dirname)
    return dirnames

def download_artifacts(artifact_type, dbnames, dirnames, force=False, dryrun=False):
    '''
    download the db in the link and store that in it's corresponding namespace directory.
    '''
    for i in range(len(dbnames)):
        logger.info("changing dir %s", dirnames[i])
        os.chdir(dirnames[i])
        logger.info(os.getcwd())
        db_filename = '%s.json' % dbnames[i]
        logger.info("checking if %s already exists: %s", db_filename, os.path.exists(db_filename))
        if not force and (os.path.exists(db_filename)):
            logger.info('not downloading %s', db_filename)
        else:
            logger.info('downloading %s to %s', dbnames[i], db_filename)
            download_cmd = artifact_download_cmd_str % (artifact_url %(artifact_type, dbnames[i]), db_filename)
            run([download_cmd], dryrun=dryrun)
    os.chdir(toplevel_dir)

def check_db_needs_conversion(dirname=None, dbname=None):
    # converted files don't exist, so convert.
    if g_args.artifact_type == "dashboard":
        if not os.path.exists(os.path.join(dirname, '%s_grafana.json' % dbname)):
            logger.info("no conversion needed for %s as converted files exist", dbname)
            return False
        logger.info("conversion needed for %s", dbname)
        return True
    if g_args.artifact_type == "alert":
        files = glob.glob(os.path.join(dirname, '%s*_cortex.yaml' % dbname))
        if len(files) > 0:
            logger.info("no conversion needed for %s as converted files (%s) exist", dbname, files)
            return False
        logger.info("conversion needed for %s", dbname)
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
        oldfile = os.path.join(dirname, '%s.json' % dbname)
        if os.path.exists(oldfile):
            newfile = os.path.join(dirname, '%s_orig.json' % dbname)
            try:
                mv_cmd = 'git mv "%s" "%s"' % (oldfile, newfile)
                run([mv_cmd], dryrun=dryrun)
            except:
                cp_cmd = 'cp "%s" "%s"' % (oldfile, newfile)
                run([cp_cmd], dryrun=dryrun)
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

def create_pr_if_needed(reviewers, branchname, additional_reviewers, dryrun=False):
    # Check if there's already a PR on this branch.
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
        generate_pr_cmd = get_generate_pr_command(reviewers, branchname)
        out = run([generate_pr_cmd], dryrun=dryrun, capture_output=True)
        output = out.stdout.decode('utf-8').strip().split('\t')
        pr_link = output[0]
        pr_num = pr_link.split('/')[-1]
    # Add the additional reviewers:
    if additional_reviewers is not None and pr_num is not None:
        if g_args.input_names is None:
            reviewers=','.join(additional_reviewers)
            logger.info("adding reviewers %s to PR %s", reviewers, pr_num)
            pr_edit_cmd='gh pr edit %s --add-reviewer %s' % (pr_num, reviewers)
            run([pr_edit_cmd])
    return pr_link

def get_filenames(dirname, artifact_name):
    if g_args.artifact_type == "dashboard":
        filenames = [
            os.path.join(dirname, '%s_orig.json' % artifact_name),
            os.path.join(dirname, '%s_wf_prom.json' % artifact_name),
            os.path.join(dirname, '%s_grafana.json' % artifact_name),
            os.path.join(dirname, '%s_summary.json' % artifact_name),
            os.path.join(dirname, 'wavefront_dashboard_link_%s.txt' % artifact_name),
            ]
        # This file doesn't get generated always.
        if os.path.exists(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%artifact_name)):
            filenames.append(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%artifact_name))
        return filenames
    else:
        assert g_args.artifact_type == "alert"
        files = glob.glob(os.path.join(dirname, '%s*_cortex.yaml' % artifact_name)) + glob.glob(os.path.join(dirname, '%s*_cortex_report.json' % artifact_name))
        return [os.path.join(dirname, '%s_orig.json' % artifact_name)] + files

def get_converter_cmd():
    return "%s/%s_converter -c %s/settings_workday_wf.yaml" % (converter_abs_dir, g_args.artifact_type, converter_abs_dir)

def get_git_commit_cmd(msg, namespace, dbname):
    return 'git commit -a -m "%s: %ss for %s-%s"' % (msg, g_args.artifact_type, namespace, dbname)

def create_prs(dblinks=[],
               dirnames=[], 
               reviewers=[], 
               namespaces=[], 
               dbnames=[],
               msg=None, 
               dryrun=False, 
               start_from=None,
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
    invalidReviewer = 0
    invalidReviewers = {}
    validReviewers = {}
    no_converted_files = 0
    conversion_failures = {}
    prs = {}
    num_prs_generated = 0
    invalid_dashboard_names = {}
    if additional_reviewers is None:
        additional_reviewers = ["owen-sullivan", "vijayarajan-k "]
    else:
        additional_reviewers = additional_reviewers + ["owen-sullivan", "vijayarajan-k "]
    if test:
        if msg is None:
            msg = 'TEST - DO NOT REVIEW'
    logger.info("starting PR creation")
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
        dblink = dblinks[i]
        dbfilename = '%s.json' % dbname
        dirname = dirnames[i]
        namespace = namespaces[i]
        logger.info("working with %s -- dirname: %s, name: %s, link: %s, namespace: %s, reviewers:%s", g_args.artifact_type, dirname, dbname, dblink, namespace, reviewers)
        if g_args.artifact_type == "dashboard":
            branchname = '%s_%ss' % (dbname, g_args.artifact_type)
        else:
            branchname = '%s_%s_%ss' % (namespace, dbname, g_args.artifact_type)
        if ' ' in branchname:
            branchname = branchname.replace(' ', '_')
        logger.info("checking out %s" % branchname)
        try:
            co_cmd = 'git checkout -b %s' % branchname
            run([co_cmd], dryrun=dryrun)
        except subprocess.CalledProcessError as cpe:
            try:
                co_cmd = 'git checkout -f %s' % branchname
                run([co_cmd], dryrun=dryrun)
            except:
                logger.error("falied to create branch %s. Moving on..", branchname)
                invalid_dashboard_names.update({dbname:dblink})
                continue
        # Rename if necessary.
        should_convert = check_db_needs_conversion(dirname=dirname, dbname=dbname)
        if (convert and should_convert) or force_convert:
            logger.info("%s converting %ss in %s (current dir:%s)", "force" if force_convert else "", g_args.artifact_type, dirname, os.getcwd())
            converter_full_cmd = '%s -f "%s"' % (get_converter_cmd(), os.path.join(dirname, dbfilename))
            try:
                run([converter_full_cmd], dryrun=dryrun)
            except subprocess.CalledProcessError as cpe:
                conversion_failures.update({dirname:traceback.format_exc()})
                continue
        # This renames the files if we already converted them.
        rename_if_necesasry(dirname=dirname, dbname=dbname, dryrun=dryrun)
        # This creates a file with link to db in it.
        create_dblink_file(dirname=dirname, dbname=dbname, dblink=dblink, dryrun=dryrun)
        filenames = get_filenames(dirname, dbname)
        git_add_cmd = 'git add %s' % ' '.join(['"%s"' % fn for fn in filenames])
        run([git_add_cmd], dryrun=dryrun)
        git_commit_cmd = get_git_commit_cmd(msg, namespace, dbname)
        try:
            run([git_commit_cmd], dryrun=dryrun)
        except subprocess.CalledProcessError as cpe:
            logger.info("failed to commit (output: %s, stderr: %s)", cpe.stdout, cpe.stderr)
            logger.info("Continuing on git commit error")
        git_pull_rebase_cmd = 'git pull --rebase origin main'
        run([git_pull_rebase_cmd], dryrun=dryrun)
        create_remote_branch_cmd = 'git push -f origin %s' % branchname
        run([create_remote_branch_cmd], dryrun=dryrun)        
        if test:
            reviewers_arg = ' '.join(['-r %s' % tr for tr in test_reviewers])
        else:
            reviewers_arg = '-r %s ' % ','.join(reviewers[i].split())
        # Only create PR if needed, otherwise push is enough.
        try:
            pr_link = create_pr_if_needed(reviewers_arg, branchname, additional_reviewers, dryrun=dryrun)
            if pr_link is not None:
                prs.update({dbname: pr_link})
        except subprocess.CalledProcessError as cpe:
            logger.info("failed to create pr for %s:%s (output: %s, stderr: %s)", g_args.artifact_type, dbname, cpe.stdout, cpe.stderr)
            # XXX: failed to create PR doesn't necessarily mean it's due to an invalid reviewer
            stderrstr = cpe.stderr.decode('utf-8').strip()
            if stderrstr.find("Could not resolve to a User") != -1:
                logger.info("invalid ghe reviewer (%s) for %s", cpe, dbname)
                invalidReviewer += 1
                invalidReviewers[reviewers[i]] = True
                logger.info("invalidReviewers: %s", invalidReviewers.keys())
            else:
                validReviewers[reviewers[i]] = True
        logger.info("validReviewers: %s", validReviewers.keys())
        num_prs_generated += 1
        co_main_cmd = 'git checkout main'
        run([co_main_cmd], dryrun=dryrun)
        if stop_on_n != 0 and num_prs_generated >= stop_on_n:
            break
    co_main_cmd = 'git checkout main'
    run([co_main_cmd], dryrun=dryrun)
    logger.info("number of invalid reviewers (non-unique): %s", invalidReviewer)
    logger.info("list of invalid reviewers (non-unique): %s", list(invalidReviewers.keys()))
    logger.info("number of branches with no converted files: %s", no_converted_files)
    logger.info("number of PRs generated: %d", num_prs_generated if not dryrun else 0)
    logger.info("encountered following failures: %s", pp.pformat(conversion_failures))
    logger.info("encountered following invalid db names: %s", pp.pformat(invalid_dashboard_names))
    logger.info("following PRs are created/exists: %s", pp.pformat(prs))
    logger.info("total # PRs created: %d", len(prs))
    os.chdir(cwd)


def exec_steps(args):
    if args.input_names is not None:
        dblinks, dbnames, namespaces, reviewers = process_input_names()
    else:
        dblinks, dbnames, namespaces, reviewers = process_csv(filename=args.input_file,
                                                          dblink_idx=5, 
                                                          reviewer_idx=10, 
                                                          namespace_idx=9, 
                                                          to_be_converted_idx=8)
    dirnames = mkdirs(namespaces)
    download_artifacts(args.artifact_type, dbnames, dirnames, dryrun=False, force=False)
    # download_dbs(dbnames, dirnames, dryrun=False, force=False)
    logger.info('number of artifacts downloaded:%s', len(dbnames))
    create_prs(dblinks=dblinks,
               dirnames=dirnames, 
               reviewers=reviewers, 
               namespaces=namespaces, 
               dbnames=dbnames,
               dryrun=args.dryrun, 
               start_from=args.start_from,               
               msg=args.message,                
               convert=(not args.skip_convert),
               force_convert = args.force_convert,
               stop_on_n=args.stop_on_n,
               test=args.test,
               test_reviewers=args.test_reviewers,
               additional_reviewers=args.additional_reviewers)

def main():
    parser = argparse.ArgumentParser(description="automate dashboard/alert download, conversion and PR generation")
    parser.add_argument('--artifact_type', default=None, type=str, choices=['dashboard', 'alert'], help="whether working with alerts or dashboards")
    parser.add_argument('--input_names', default=None, action="append", help="list of artifact ids - exclusive with input_file")
    parser.add_argument('--use_namespace', default=None, help="provide the service_team name. Use test if testing. Required when using --input-names")
    parser.add_argument('--input_file', default='WavefrontDashboards_all.csv', help="file containing the artifact details. exclusive with input_names")
    parser.add_argument('--start_from', default=None, help="start from this artifact name, ignoring everything before it")
    parser.add_argument('--stop_on_n', default=0, type=int, help='stop after generating n artifacts')
    parser.add_argument('--skip_convert', default=False, action="store_true", help="skip conversion")
    parser.add_argument('--force_convert', default=False, action="store_true", help="force conversion")
    parser.add_argument('--test_reviewers', default=None, help='use this reviewer for test runs', action="append")
    parser.add_argument('--additional_reviewers', default=None, help='additional reviewers to include', action="append")
    parser.add_argument('--test', default=False, action='store_true', help='test automate process')
    parser.add_argument('--message', default="Conversion")
    parser.add_argument('--dryrun', default=False, action="store_true", help="dryrun mode, just prints the steps/commands")
    args = parser.parse_args()
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
    # pdb.runcall(exec_steps)
    main()
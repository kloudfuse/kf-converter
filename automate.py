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


toplevel_dir = os.path.join(os.getcwd())
logger.info(toplevel_dir)
download_cmd_str = "curl 'https://workday.wavefront.com/api/v2/dashboard/%s' -H 'Authorization: Bearer 537fdad9-f66a-4c79-ad81-f9c3faf1ee72' | jq > \"%s\""
converter_abs_dir = os.path.join(os.path.abspath(toplevel_dir), os.path.pardir, 'conversions-binary')
logger.info(converter_abs_dir)
converter_cmd = "%s/dashboard_converter -c %s/settings_workday_wf.yaml" % (converter_abs_dir, converter_abs_dir)
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
        dirname = os.path.join(toplevel_dir, ns, 'wf-dashboards')
        logger.info('creating dir %s', dirname)
        os.makedirs(dirname, exist_ok=True)
        dirnames.append(dirname)
    return dirnames

def download_dbs(dbnames, dirnames, force=False, dryrun=False):
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
            download_cmd = download_cmd_str % (dbnames[i], db_filename)
            run([download_cmd], dryrun=dryrun)
    os.chdir(toplevel_dir)

def check_db_needs_conversion(dirname=None, dbname=None):
    # converted files don't exist, so convert.
    if not os.path.exists(os.path.join(dirname, '%s_grafana.json' % dbname)):
        logger.info("no conversion needed for %s as converted files exist", dbname)
        return True
    logger.info("conversion needed for %s", dbname)
    return False

def rename_if_necesasry(dirname=None, dbname=None, dryrun=False):
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
    
def create_dblink_file(dirname=None, dbname=None, dblink=None, dryrun=False):
    # Create link file.
    link_file = os.path.join(dirname, 'wavefront_dashboard_link_%s.txt' % dbname)
    if os.path.exists(link_file):
        return
    logger.info("writing db link (%s) to file %s", dblink, link_file)
    write_linkfile_cmd = 'echo "%s" >> "%s"' % (dblink, link_file)
    run([write_linkfile_cmd], dryrun=dryrun)

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
        pr_link = 'https://ghe.megaleo.com/wavefront-migration/dashboards/pull/%s' % pr_num
        logger.info('a pr on branch %s already exists and is %s: %s', branchname, pr_status, pr_link)
    else:
        generate_pr_cmd = 'gh pr create %s -f --head %s --base main' % (reviewers, branchname)
        out = run([generate_pr_cmd], dryrun=dryrun, capture_output=True)
        output = out.stdout.decode('utf-8').strip().split('\t')
        pr_link = output[0]
        pr_num = pr_link.split('/')[-1]
    # Add the additional reviewers:
    if additional_reviewers is not None and pr_num is not None:
        reviewers=','.join(additional_reviewers)
        logger.info("adding reviewers %s to PR %s", reviewers, pr_num)
        pr_edit_cmd='gh pr edit %s --add-reviewer %s' % (pr_num, reviewers)
        run([pr_edit_cmd])
    return pr_link

def create_prs(dblinks=[],
               dirnames=[], 
               reviewers=[], 
               namespaces=[], 
               dbnames=[],
               msg="TEST - DO NOT REVIEW", 
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
        logger.info("processing %s", dbname)
        dblink = dblinks[i]
        dbfilename = '%s.json' % dbname
        dirname = dirnames[i]
        namespace = namespaces[i]
        branchname = '%s_dashboards' % dbname
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
            logger.info("%s converting dashboards in %s (current dir:%s)", "force" if force_convert else "", dirname, os.getcwd())
            converter_full_cmd = '%s -f "%s"' % (converter_cmd, os.path.join(dirname, dbfilename))
            try:
                run([converter_full_cmd], dryrun=dryrun)
            except subprocess.CalledProcessError as cpe:
                conversion_failures.update({dirname:traceback.format_exc()})
                continue
        if not os.path.exists(os.path.join(dirname, '%s_grafana.json' % dbname)):
            no_converted_files += 1
            continue
        # This renames the files if we already converted them.
        rename_if_necesasry(dirname=dirname, dbname=dbname, dryrun=dryrun)
        # This creates a file with link to db in it.
        create_dblink_file(dirname=dirname, dbname=dbname, dblink=dblink, dryrun=dryrun)
        filenames = [
            os.path.join(dirname, '%s_orig.json' % dbname),
            os.path.join(dirname, '%s_wf_prom.json' % dbname),
            os.path.join(dirname, '%s_grafana.json' % dbname),
            os.path.join(dirname, '%s_summary.json' % dbname),
            os.path.join(dirname, 'wavefront_dashboard_link_%s.txt' % dbname),
            ]
        # This file doesn't get generated always.
        if os.path.exists(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%dbname)):
            filenames.append(os.path.join(dirname, '%s_dashboard_wrapped_grafana.json'%dbname))
        git_add_cmd = 'git add %s' % ' '.join(['"%s"' % fn for fn in filenames])
        run([git_add_cmd], dryrun=dryrun)
        git_commit_cmd = 'git commit -a -m "%s: dashboards for %s-%s"' % (msg, namespace, dbname)
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
            logger.info("failed to create pr for dasboard (output: %s, stderr: %s)", dbname, cpe.stdout, cpe.stderr)
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
    logger.info("number of invalid reviewers (non-unique): %s", invalidReviewer)
    logger.info("number of branches with no converted files: %s", no_converted_files)
    logger.info("number of PRs generated: %d", num_prs_generated if not dryrun else 0)
    logger.info("encountered following failures: %s", pp.pformat(conversion_failures))
    logger.info("encountered following invalid db names: %s", pp.pformat(invalid_dashboard_names))
    logger.info("following PRs are created/exists: %s", pp.pformat(prs))
    os.chdir(cwd)


def exec_steps(args):
    dblinks, dbnames, namespaces, reviewers = process_csv(filename=args.input_file,
                                                          dblink_idx=5, 
                                                          reviewer_idx=10, 
                                                          namespace_idx=9, 
                                                          to_be_converted_idx=8)
    dirnames = mkdirs(namespaces)
    download_dbs(dbnames, dirnames, dryrun=False, force=False)
    # download_dbs(dbnames, dirnames, dryrun=False, force=False)
    logger.info('number of dashboards downloaded:%s', len(dbnames))
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
    parser.add_argument('--input_file', default='WavefrontDashboards_all.csv', help="file containing the artifact details")
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
        assert args.test_reviewers is not None
    exec_steps(args)

if __name__ == "__main__":
    # pdb.runcall(exec_steps)
    main()
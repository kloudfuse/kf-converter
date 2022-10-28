#!/usr/bin/python3
# This script automates the steps to generate PR. The script takes following steps:
# 1. Processes the csv file containing the dashboard links to retrieve 'namespaces', dashboard names and links, reviewers.
# 2. Create namespace directories
# 3. download dashboards
# 4. TODO: Conversion
# 5. generate PRs.

import csv
import subprocess
import os
import glob
import pdb

toplevel_dir = os.path.join(os.getcwd())
print(toplevel_dir)
download_cmd_str = "curl 'https://workday.wavefront.com/api/v2/dashboard/%s' -H 'Authorization: Bearer 537fdad9-f66a-4c79-ad81-f9c3faf1ee72' | jq > \"%s\".json"
converter_abs_dir = os.path.join(os.path.abspath(toplevel_dir), os.path.pardir, 'conversions-binary')
print(converter_abs_dir)
converter_cmd = "%s/dashboard_converter -c %s/settings_workday_wf.yaml" % (converter_abs_dir, converter_abs_dir)
def run(cmd, dryrun=False, check=True, shell=True, capture_output=False, timeout=None, *args):
    assert not(dryrun and capture_output) or dryrun ^ capture_output,\
        "Only one of dryrun/capture_output should be set; fix the code"
    print("{}".format(' '.join(cmd)))
    if dryrun:
        return
    try:
        return subprocess.run(cmd, check=check, shell=shell, timeout=timeout,
                              capture_output=capture_output, *args)
    except subprocess.TimeoutExpired:
        if timeout is not None:
            print("\"{}\" timed out".format(' '.join(cmd)))
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
    with open(filename, newline='') as csvfile:
        csvreader = csv.reader(csvfile)
        i = 0
        for row in csvreader:
            print(', '.join(row))
            dblink = row[dblink_idx].strip()
            reviewer = row[reviewer_idx].strip()
            namespace = row[namespace_idx].strip()
            to_be_converted = row[to_be_converted_idx]
            if not dblink.startswith('https'):
                print('ignoring', row)
                continue
            if reviewer == "":
                print("skipping db %s at line %d for empty reviewer" % (dblink, i))
                continue
            if to_be_converted != 'TRUE':
                print('skipping db', dblink, 'at line', i, 'as it is not to be converted')
                continue
            print(dblink, to_be_converted, namespace, reviewer)
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
        print('creating dir', dirname)
        os.makedirs(dirname, exist_ok=True)
        dirnames.append(dirname)
    return dirnames

def download_dbs(dbnames, dirnames, force=False, dryrun=False):
    '''
    download the db in the link and store that in it's corresponding namespace directory.
    '''
    for i in range(len(dbnames)):
        print("changing dir", dirnames[i])
        os.chdir(dirnames[i])
        print(os.getcwd())
        if not os.path.exists('%s.json' % dbnames[i]) or force:
            print('downloading', dbnames[i], 'to', '%s.json' % dbnames[i])
            download_cmd = download_cmd_str % (dbnames[i], dbnames[i])
            run([download_cmd], dryrun=dryrun)
        else:
            print('not downloading', '%s.json' % dbnames[i])
    os.chdir(toplevel_dir)

def create_prs(dirnames=[], reviewers=[], namespaces=[], dbnames=[], msg="TEST - DO NOT REVIEW", dryrun=True, generate=True):
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
    for i in range(len(dbnames)):
        dbname = dbnames[i]
        dirname = dirnames[i]
        namespace = namespaces[i]
        branchname = '%s_dashboards' % dbname
        print("checking out %s" % branchname)
        try:
            co_cmd = 'git checkout -b %s' % branchname
            run([co_cmd], dryrun=dryrun)
        except subprocess.CalledProcessError as cpe:
            co_cmd = 'git checkout -f %s' % branchname
            run([co_cmd], dryrun=dryrun)
        if generate:
            print("converting dashboards in %s", dirname)
            converter_full_cmd = '%s -d %s' % (converter_cmd, dirname)
            run([converter_full_cmd], dryrun=dryrun)
            # Do actual conversion now.
        if not os.path.exists(os.path.join(dirname, '%s_grafana.json' % dbname)):
            no_converted_files += 1
            continue

        filenames = glob.glob(os.path.join(dirname, '%s*.json' % dbname))
        git_add_cmd = 'git add %s' % ' '.join(filenames)
        run([git_add_cmd], dryrun=dryrun)
        git_commit_cmd = 'git commit -a -m "%s: dashboards for %s-%s"' % (msg, namespace, dbname)
        try:
            run([git_commit_cmd], dryrun=dryrun)
        except subprocess.CalledProcessError as cpe:
            print("Continuing on git commit error")
        git_pull_rebase_cmd = 'git pull --rebase origin master'
        run([git_pull_rebase_cmd], dryrun=dryrun)
        create_remote_branch_cmd = 'git push -f origin %s' % branchname
        run([create_remote_branch_cmd], dryrun=dryrun)
        generate_pr_cmd = 'gh pr create -r %s -f --head %s --base master' % (reviewers[i], branchname)
        try:
            run([generate_pr_cmd], dryrun=dryrun)
        except subprocess.CalledProcessError as cpe:
            print("invalid ghe reviewer", cpe, dbname)
            invalidReviewer += 1
            invalidReviewers[reviewers[i]] = True
            print("invalidReviewers: ", invalidReviewers.keys())
        else:
            validReviewers[reviewers[i]] = True
        print("validReviewers: ", validReviewers.keys())
        co_main_cmd = 'git checkout master'
        run([co_main_cmd], dryrun=dryrun)
    print("number of invalid reviewers (non-unique): ", invalidReviewer)
    print("number of branches with no converted files: ", no_converted_files)
    os.chdir(cwd)


def exec_steps():
    dblinks, dbnames, namespaces, reviewers = process_csv(filename='WavefrontDashboards_all.csv',
                                dblink_idx=5, reviewer_idx=10, namespace_idx=9, to_be_converted_idx=8)
    print('following dbs will be converted')
    for i in range(len(dblinks)):
        print(dbnames[i], namespaces[i], reviewers[i])
    dirnames = mkdirs(namespaces)
    download_dbs(dbnames, dirnames, dryrun=False, force=True)
    print('number of dashboards downloaded:', len(dbnames))
    create_prs(dirnames=dirnames, reviewers=reviewers, namespaces=namespaces, dbnames=dbnames, dryrun=False, msg="Conversion", generate=False)

if __name__ == "__main__":
    # pdb.runcall(exec_steps)
    exec_steps()

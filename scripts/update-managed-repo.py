#!/usr/bin/env python3

from os.path import isfile
from subprocess import run

run(('pip', 'install', '--upgrade', 'gitpython'))
from git import Repo


def run_extra_script(at):
    if isfile(at):
        result = run(at)
        if result.returncode != 0:
            exit(result.returncode)


if __name__ == '__main__':
    # open and prepare repo
    repo = Repo()
    repo.git.fetch()

    # exit if no pull required
    if isfile('.git/scramjet-setup-complete') and (
            repo.head.is_detached or repo.head.commit == repo.remotes.origin.refs[repo.active_branch.name].commit
    ):
        print('Repo already at latest version, exiting...')
        exit(0)

    # pull repo and run extra scripts
    run_extra_script('.git/hooks/pre-pull')
    if not repo.head.is_detached:
        repo.git.pull('--recurse-submodules')
    run_extra_script('.git/hooks/post-pull')

    # mark repo as updated
    open('.git/scramjet-setup-complete', 'w').close()

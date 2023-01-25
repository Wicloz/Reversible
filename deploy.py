#!/usr/bin/env python3
import readline

from os import listdir
from git import Repo
from os.path import exists
from sys import argv
from package import Package

if __name__ == '__main__':
    # Setup git repo
    repo = Repo()
    repo.git.reset()

    # Determine packages to deploy
    packages = listdir()
    if argv[1] == 'custom':
        packages = input('Enter Package Names: ').split(' ')

    # Iterate packages with changes
    for package in packages:
        if exists(package + '/DEBIAN.YML'):
            repo.git.add(package)

            # Build and deploy package
            if argv[1] in ('custom', 'all') or repo.index.diff(repo.head.commit):
                input(f'Press ⏎ to Deploy "{package}" ...')
                pkg = Package(package)
                pkg.build()
                pkg.deploy()

            # Commit changes to package
            repo.git.add(package + '/version')
            if repo.index.diff(repo.head.commit):
                repo.git.commit(message=input('Enter Commit Message: '))

    # Push all changes to remote
    repo.git.push()

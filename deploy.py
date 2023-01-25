#!/usr/bin/env python3
import readline

from os import listdir
from git import Repo
from os.path import exists, isdir
from sys import argv
from package import Package

if __name__ == '__main__':
    # Process file name
    _, mode = argv[0].split('-')
    assert mode in {'custom', 'changed', 'all'}

    # Setup git repo
    repo = Repo()
    repo.git.reset()

    # Ask for package names when needed
    if mode == 'custom':
        packages = input('Enter Package Names: ').split(' ')

    # Iterate over categories
    for category in listdir():
        if not isdir(category):
            continue

        # Determine actual packages to deploy
        if mode != 'custom':
            packages = listdir(category)

        # Iterate packages with changes
        for package in packages:
            if exists(category + '/' + package + '/DEBIAN.YML'):
                repo.git.add(category + '/' + package)

                # Build and deploy package
                if mode in {'custom', 'all'} or repo.index.diff(repo.head.commit):
                    input(f'Press ⏎ to Deploy "{package}" ...')
                    pkg = Package(category + '/' + package)
                    pkg.build()
                    pkg.deploy()

                # Commit changes to package
                repo.git.add(category + '/' + package + '/version')
                if repo.index.diff(repo.head.commit):
                    repo.git.commit(message=input('Enter Commit Message: '))

    # Push all changes to remote
    repo.git.push()

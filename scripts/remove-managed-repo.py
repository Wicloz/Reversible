#!/usr/bin/env python3

from sys import argv
from subprocess import run
from os import unlink, rmdir, listdir
from os.path import isfile, join, islink, isdir
from shutil import rmtree

run(('pip', 'install', '--upgrade', 'gitpython'))
from git import Repo, InvalidGitRepositoryError


def recursive_tree_delete(node):
    if node.type == 'tree':
        for child in node:
            recursive_tree_delete(child)
        if not listdir(node.abspath):
            rmdir(node.abspath)
    elif islink(node.abspath) or isfile(node.abspath):
        unlink(node.abspath)


def recursive_module_delete(module):
    for submodule in module.submodules:
        recursive_module_delete(Repo(submodule.abspath))

    if module.refs:
        recursive_tree_delete(module.head.commit.tree)

    rmtree(module.git_dir)
    if isfile(join(module.working_dir, '.git')):
        unlink(join(module.working_dir, '.git'))


def try_remove_repo(path):
    if not isdir(path):
        print(f'Skipping non-existing Git repository at "{path}"!')
        return

    try:
        recursive_module_delete(Repo(path))
    except InvalidGitRepositoryError:
        print(f'Skipping invalid Git repository at "{path}"!')

    if not listdir(path):
        rmdir(path)


if __name__ == '__main__':
    _, target = argv
    try_remove_repo(target)

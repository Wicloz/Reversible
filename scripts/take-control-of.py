#!/usr/bin/env python3

from sys import argv
from hashlib import md5

if __name__ == '__main__':
    _, package, target = argv

    md5sum = md5()
    with open(target, 'rb') as fp:
        for chunk in iter(lambda: fp.read(2047), b''):
            md5sum.update(chunk)
    md5sum = md5sum.hexdigest()

    lines = {''}
    with open(f'/var/lib/dpkg/info/{package}.list', 'r') as fp:
        for line in fp:
            lines.add(line.strip())

    parent = target
    with open(f'/var/lib/dpkg/info/{package}.list', 'a') as fp:
        while parent not in lines:
            fp.write(parent + '\n')
            parent = parent.rsplit('/', 1)[0]

    with open(f'/var/lib/dpkg/info/{package}.md5sums', 'r') as fp:
        lines = fp.readlines()

    written = False
    with open(f'/var/lib/dpkg/info/{package}.md5sums', 'w') as fp:
        for line in lines:
            if line.split('  ')[1] == target[1:] + '\n':
                fp.write(md5sum + '  ' + target[1:] + '\n')
                written = True
            else:
                fp.write(line)
        if not written:
            fp.write(md5sum + '  ' + target[1:] + '\n')

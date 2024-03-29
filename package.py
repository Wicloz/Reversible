from modules import *
from pathlib import Path, PurePath
from tempfile import TemporaryDirectory
from os import readlink, remove
from subprocess import run
from ruamel.yaml import YAML
from inspect import cleandoc


class Package:
    MODULES = [
        ControlFile,
        CopyFiles,
        SecureFiles,
        Triggers,
        CompressGzip,
        AutoDiversions,
        SystemUsers,
        PackageManagers,
        OpenPorts,
        DNS,
        ReverseProxy,
        SharedFolders,
        AptSources,
        ManageDBs,
        WebSites,
        ApplyPatches,
        MuninPlugins,
        BorgCacheDir,
        UserScripts,
        GitRepo,
        SystemdUnits1, SystemdUnits2,
        DockerContainers,
        ClaimFiles,
    ]
    LOADER = YAML(typ='unsafe').load

    def __init__(self, name):
        self.package = Path(name).resolve()

    def deploy(self):
        targets = []
        with open(self.package / '..' / 'hosts', 'r') as fp:
            for line in fp:
                line = line.split('#')[0].strip()
                if line:
                    targets.append(line)

        for target in targets:
            run(('scp', f'/tmp/{self.package.name}.deb', f'{target}:/tmp/{self.package.name}.deb'))
            run(args=('ssh', target, 'bash -'), input=cleandoc(f"""
                sudo DEBIAN_FRONTEND=noninteractive apt-get -yq update
                sudo DEBIAN_FRONTEND=noninteractive apt-get -yq remove {self.package.name}
                sudo DEBIAN_FRONTEND=noninteractive apt-get -yq install /tmp/{self.package.name}.deb
                rm /tmp/{self.package.name}.deb
            """).encode('UTF8'))

        remove(f'/tmp/{self.package.name}.deb')

    def build(self):
        with TemporaryDirectory() as temp:
            temp = Path(temp)

            # prepare build folder
            (temp / 'DEBIAN').mkdir()

            # construct modules in order
            modules = [M(self.package, temp) for M in self.MODULES]

            # build cache of special files
            patterns = {'purge.sh', 'preinst.sh', 'postinst.sh', 'prerm.sh', 'postrm.sh', 'version'}

            special = set()
            for pattern in patterns:
                for match in self.package.glob(pattern):
                    special.add(match)

            yaml = set()
            for pattern in BaseModule.YAML:
                for match in self.package.glob(pattern):
                    yaml.add(match)

            # process all source files
            for path in self.package.glob('**/*'):
                absolute = PurePath('/') / path.relative_to(self.package)

                # skip special files
                if path in special:
                    continue

                # manage YAML files
                if path in yaml:
                    content = self.LOADER(path)
                    for module in modules:
                        module.process_yaml(absolute, content)
                    continue

                # give symlinks to all modules
                if path.is_symlink():
                    target = PurePath(readlink(path))
                    for module in modules:
                        module.process_symlink(absolute, target)
                    continue

                # give files to all modules
                if path.is_file():
                    with open(path, 'rb') as fp:
                        for module in modules:
                            module.process_file(absolute, fp)
                            fp.seek(0)
                    continue

            # load and increment package version
            if (self.package / 'version').exists():
                with open(self.package / 'version', 'r') as fp:
                    version = int(fp.read()) + 1
            else:
                version = 1

            # construct control file from modules
            combined = {
                'version': {str(version)},
            }

            for module in modules:
                for key, values in module.control.items():
                    if key not in combined:
                        combined[key] = set()
                    combined[key].update(values)

            with open(temp / 'DEBIAN' / 'control', 'w') as fp:
                for key, values in combined.items():
                    fp.write(key.title() + ': ' + ', '.join(values) + '\n')

            # combine module script trackers
            scripts = ModuleScripts()
            for module in modules:
                scripts += module.scripts

            # write actual package scripts
            for phase in ('preinst', 'postinst', 'prerm', 'postrm'):
                content = []

                if phase in {'postinst', 'postrm'}:
                    content += scripts.prepares

                content += scripts.stages[phase]

                if phase in {'postinst', 'postrm'}:
                    content += scripts.triggers

                if content or (phase == 'postrm' and scripts.purges):
                    with open(temp / 'DEBIAN' / phase, 'w') as fp:
                        fp.write('#!/bin/bash')
                        if phase in {'preinst', 'prerm'}:
                            fp.write('\nset -e')
                        if phase == 'postrm':
                            fp.write('\n\nif [[ "$1" == "purge" ]]; then')
                            fp.writelines((
                                '\n\n(\n' + item + '\n)' for item in scripts.purges
                            ))
                            fp.write('\n\nexit 0; fi')
                        fp.writelines((
                            '\n\n(\n' + item + '\n)' for item in content
                        ))
                        fp.write('\n\nexit 0\n')
                    (temp / 'DEBIAN' / phase).chmod(0o755)

            # use dpkg to build .deb archive
            run(('dpkg-deb', '--root-owner-group', '-Zxz', '--build', temp, '/tmp/' + self.package.name + '.deb'))

            # save new version after successful build
            with open(self.package / 'version', 'w') as fp:
                fp.write(str(version))

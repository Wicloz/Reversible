from abc import ABC
from shutil import copyfileobj, copystat
import magic
from itertools import chain, count
from inspect import cleandoc, getfullargspec
import requests
from utils import Scripts
from slugify import slugify
from copy import deepcopy
from pathlib import PurePath, Path
from subprocess import run
from contextlib import contextmanager
from secrets import choice
import gzip
import json


class BaseModule(ABC):
    YAML = {'DEBIAN.YML', '**/.git.yml'}
    LISTENERS = []

    def __init__(self, source, target):
        self.source = source
        self.target = target
        self.control = {}
        self.scripts = Scripts()
        self.LISTENERS.append(self.on_file_write)

    def process_yaml(self, path, content):
        for i in count(1):
            func = '_parse_' + slugify(text=path.name, separator='_') + '_' + str(i)

            if not hasattr(self, func):
                break
            func = getattr(self, func)

            kwargs = {}
            for key in getfullargspec(func)[0][2:]:
                kwargs[key] = deepcopy(content[key]) if key in content else None
            if not all(kwarg is None for kwarg in kwargs.values()):
                func(path, **kwargs)

    def process_file(self, path, fp):
        pass

    def process_symlink(self, path, content):
        pass

    def on_file_write(self, remote, local):
        pass

    @contextmanager
    def prepare(self, absolute, mode=None, like=None):
        absolute = PurePath(absolute)

        output = self.target / absolute.relative_to('/')
        output.parent.mkdir(parents=True, exist_ok=True)
        yield output

        if like is not None:
            copystat(
                src=self.source / PurePath(like).relative_to('/'),
                dst=output,
                follow_symlinks=False,
            )

        if not output.is_symlink():
            output.chmod(mode)
        elif mode is not None:
            raise NotImplementedError('Tried to set permissions on symlink!')

        for handler in self.LISTENERS:
            handler(absolute, output)

    @contextmanager
    def write(self, absolute, executable):
        with self.prepare(absolute, 0o755 if executable else 0o644) as output:
            with open(output, 'w') as fp:
                yield fp

    def systemd_reload(self, unit):
        self.scripts.trigger(
            f'systemctl try-reload-or-restart {unit}', False,
        )

    @staticmethod
    def token(size):
        alphabet = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        return ''.join(choice(alphabet) for _ in range(size))


class ControlFile(BaseModule):
    def _parse_debian_yml_1(self, _, description, apt, depreciates):
        self.control = {
            'package': [self.source.name],
            'description': [description],
            'architecture': ['all'],
            'maintainer': ['Wilco de Boer <deboer.wilco@gmail.com>'],
            'section': ['scramjet'],
        }

        if apt is not None:
            self.control['depends'] = apt

        if depreciates is not None:
            self.control['provides'] = depreciates
            self.control['conflicts'] = depreciates
            self.control['replaces'] = depreciates


class SecureFiles(BaseModule):
    def _parse_debian_yml_1(self, _, secure):
        for user, paths in secure.items():
            for path in paths:
                self.scripts.install(cleandoc(f"""
                    chmod -077 "{path}"
                    chown {user}:{user} "{path}"
                """), False, 'after')


class CopyFiles(BaseModule):
    def __init__(self, source, target):
        super().__init__(source, target)
        self.secure = set()

    def _parse_debian_yml_1(self, _, secure):
        self.secure = set(chain.from_iterable(secure.values()))

    def process_file(self, path, fp):
        # determine some file properties
        shebang = fp.read(3)
        if len(shebang) == 3:
            mime = magic.from_buffer(shebang + fp.read(1048573), True).split('/')[0]
        else:
            mime = None

        # determine correct file mode
        mode = 0o755
        if shebang != b'#!/' and mime != 'application':
            mode &= 0o666
        if str(path) in self.secure:
            mode &= 0o700

        # reset file pointer
        fp.seek(0)

        # copy the file and set properties
        with self.prepare(path, mode, path) as file:
            with file.open('wb') as out:
                if mime == 'text':
                    for line in fp:
                        out.write(line.replace(b'\r\n', b'\n').replace(b'\r', b'\n'))
                else:
                    copyfileobj(fp, out)

    def process_symlink(self, path, content):
        with self.prepare(path, None, path) as file:
            file.symlink_to(content)


class PackageManagers(BaseModule):
    def _parse_debian_yml_1(self, _, pip, npm):
        with self.write(f'/lib/systemd/system/{self.source.name}.timer', False) as fp:
            fp.write(cleandoc(f"""
                [Unit]
                Description=update process for Python/Node packages of "{self.source.name}"
                [Timer]
                OnCalendar=daily
                Persistent=true
                RandomizedDelaySec=1h
                [Install]
                WantedBy=timers.target
            """))

        with self.write(f'/lib/systemd/system/{self.source.name}.service', False) as fp:
            fp.write(cleandoc(f"""
                [Unit]
                Description=update process for Python/Node packages of "{self.source.name}"
                [Service]
                Type=oneshot
            """))
            if pip is not None:
                fp.write('\nExecStart=/usr/bin/pip3 install --upgrade ' + ' '.join(pip))
            if npm is not None:
                fp.write('\nExecStart=/usr/bin/npm install --global --production ' +
                         ' '.join(item + '@latest' for item in npm))

        self.scripts.install(
            f'systemctl start {self.source.name}.service',
            False, 'after',
        )

        self.control['pre-depends'] = []
        if pip is not None:
            self.control['pre-depends'].append('python3-pip')
        if npm is not None:
            self.control['pre-depends'].append('npm')


class OpenPorts(BaseModule):
    def _parse_debian_yml_1(self, _, firewall):
        self.control['pre-depends'] = []

        if 'block' in firewall:
            self.control['pre-depends'] += ['ufw']
            for port in firewall['block']:
                self.scripts.install(f'ufw deny out {port}', f'ufw delete deny out {port}')

        if 'internal' in firewall:
            self.control['pre-depends'] += ['ufw']
            for port in firewall['internal']:
                self.scripts.install(cleandoc(f"""
                    ufw allow from 192.168.0.0/16 to any port {port}
                    ufw allow from fe80::/10 to any port {port}
                """), cleandoc(f"""
                    ufw delete allow from 192.168.0.0/16 to any port {port}
                    ufw delete allow from fe80::/10 to any port {port}
                """))

        if 'external' in firewall:
            self.control['pre-depends'] += ['ufw', 'miniupnpc']

            for port in firewall['external']:
                self.scripts.install(
                    f'ufw allow {port}',
                    f'ufw delete allow {port}',
                )

            with self.write(f'/etc/cron.d/{self.source.name}', False) as fp:
                for port in firewall['external']:
                    enable = (
                        f'upnpc -z {{}} -a 192.168.68.105 {port} {port} tcp',
                        f'upnpc -z {{}} -a 192.168.68.105 {port} {port} udp',
                    )
                    disable = (
                        f'upnpc -z {{}} -d {port} tcp',
                        f'upnpc -z {{}} -d {port} udp',
                    )

                    for code in enable:
                        fp.write(f'* * * * * root ' + code.format(1900) + ' &> /dev/null' + '\n')

                    self.scripts.install(*[
                        '\n'.join(chain(
                            ['ufw allow from 192.168.68.1 to any port 1901'],
                            [code.format(1901) for code in action],
                            ['ufw delete allow from 192.168.68.1 to any port 1901'],
                        )) for action in (enable, disable)
                    ], 'before')


class DNS(BaseModule):
    def on_file_write(self, path, _):
        if path.parent in {PurePath('/etc/nginx/sites-enabled/'), PurePath('/etc/cloudflare/records/')}:
            self.scripts.trigger('systemctl restart ddns.service', False)

    def _parse_debian_yml_1(self, _, cloudflare):
        with self.write(f'/etc/cloudflare/records/{self.source.name}.json', False) as fp:
            json.dump(cloudflare, fp)


class ReverseProxy(BaseModule):
    def _parse_debian_yml_1(self, _, proxies):
        self.systemd_reload('nginx.service')

        for proxy in proxies:
            proxy.setdefault('restricted', False)
            proxy.setdefault('logging', True)
            proxy.setdefault('headers', [])
            proxy.setdefault('static', [])
            proxy.setdefault('same-origin', True)
            proxy.setdefault('address', '127.0.0.1')
            proxy.setdefault('redirects', [])

            if 'timeout' not in proxy:
                timeout = '30s'
            elif proxy['timeout']:
                timeout = proxy['timeout']
            else:
                timeout = '1h'

            includes = ['listen']
            if proxy['cloudflare']:
                includes.append('cloudflare')
            if proxy['restricted']:
                includes.append('restricted')

            if proxy['same-origin']:
                proxy['headers'] += [
                    'X-Frame-Options: SAMEORIGIN',
                    'Referrer-Policy: same-origin',
                    'Cross-Origin-Opener-Policy: same-origin',
                    'Cross-Origin-Resource-Policy: same-origin',
                ]

            with self.write('/etc/nginx/sites-available/' + proxy['name'] + '.conf', False) as fp:
                fp.write('server {\n')
                for include in includes:
                    fp.write(f'    include /etc/nginx/snippets/{include}.conf;\n')
                if includes:
                    fp.write('\n')

                fp.write('    server_name ' + ' '.join(proxy['domains']) + ';\n')
                if proxy['logging']:
                    fp.write('    error_log /var/log/nginx/' + proxy['name'] + '.error.log;\n')
                    fp.write('    access_log /var/log/nginx/' + proxy['name'] + '.access.log;\n')
                else:
                    fp.write('    error_log /dev/null;\n')
                    fp.write('    access_log /dev/null;\n')
                fp.write('\n')

                fp.write(f'    proxy_read_timeout {timeout};\n')
                fp.write(f'    proxy_send_timeout {timeout};\n')
                fp.write('\n')

                for header in proxy['headers']:
                    fp.write(f'    more_set_headers "{header}";\n')
                if proxy['headers']:
                    fp.write('\n')

                for redirect in proxy['redirects']:
                    fp.write('    location = ' + redirect['from'] + ' {\n')
                    fp.write('        return ' + (
                        '308' if redirect['permanent'] else '307'
                    ) + ' ' + redirect['to'] + ';\n')
                    fp.write('    }\n')
                    fp.write('\n')

                for static in proxy['static']:
                    fp.write('    location ' + static['location'] + ' {\n')
                    if 'root' in static:
                        fp.write('        root "' + static['root'] + '";\n')
                    if 'alias' in static:
                        fp.write('        alias "' + static['alias'] + '";\n')
                    if 'expires' in static:
                        fp.write('        expires ' + static['expires'] + ';\n')
                    fp.write('    }\n')
                    fp.write('\n')

                fp.write('    location / {\n')
                if 'root' in proxy:
                    if 'expires' in proxy:
                        fp.write('        expires ' + proxy['expires'] + ';\n')
                    fp.write('        root "' + proxy['root'] + '";\n')
                    fp.write('        try_files $uri @proxy;\n')
                    fp.write('    }\n')
                    fp.write('\n')

                    fp.write('    location @proxy {\n')
                if 'php' in proxy:
                    fp.write('        fastcgi_pass ' + proxy['address'] + ':' + str(proxy['port']) + ';\n')
                    fp.write('        include /etc/nginx/fastcgi_params;\n')
                    fp.write('        fastcgi_param SCRIPT_FILENAME "' + proxy['php'] + '";\n')
                else:
                    fp.write('        proxy_pass http://' + proxy['address'] + ':' + str(proxy['port']) + ';\n')
                fp.write('    }\n')

                # end of nginx file
                fp.write('}\n')

            with self.prepare('/etc/nginx/sites-enabled/' + proxy['name'] + '.conf') as enabled:
                enabled.symlink_to('../sites-available/' + proxy['name'] + '.conf')


class WebSites(BaseModule):
    def on_file_write(self, path, _):
        if path.parent == PurePath('/etc/nginx/sites-enabled/'):
            self.systemd_reload('nginx.service')
        if path.parent == PurePath('/etc/apache2/sites-enabled/'):
            self.systemd_reload('apache2.service')


class SharedFolders(BaseModule):
    def _manage_local_folder(self, owner, folder):
        parent = PurePath(folder).parent

        self.scripts.install(cleandoc(f"""
            mkdir -p "{folder}"
            chown {owner}:{owner} "{folder}"
        """), cleandoc(f"""
            rmdir -p "{folder}"
        """), 'before')

        self.scripts.purge(cleandoc(f"""
            if [ -e "{folder}" ]; then
                rm -r "{folder}"
                rmdir -p "{parent}"
            fi
        """))

    def _parse_debian_yml_1(self, _, folders):
        for user, paths in folders.items():
            for path in paths:
                self._manage_local_folder(user, path)

    def _parse_debian_yml_2(self, _, shares):
        for share in shares:
            share.setdefault('user', 'smbguest')
            share['writable'] = 'yes' if share.get('writable', True) else 'no'

            if 'password' in share:
                self.scripts.install(
                    'yes "{password}" | smbpasswd -a "{user}"'.format(**share),
                    'smbpasswd -x "{user}"'.format(**share),
                )

            with self.write('/etc/samba/shares/' + share['name'] + '.conf', False) as fp:
                fp.write(cleandoc("""
                    [{name}]
                    path = {path}
                    force user = {user}
                    writable = {writable}
                """.format(**share)))
                fp.write('\n')

                if 'password' in share:
                    fp.write(cleandoc("""
                        valid users = {user}
                    """.format(**share)))
                else:
                    fp.write(cleandoc("""
                        guest ok = yes
                        guest only = yes
                    """.format(**share)))
                fp.write('\n')

            self._manage_local_folder(share['user'], share['path'])


class AptSources(BaseModule):
    def _parse_debian_yml_1(self, _, sources):
        for name, values in sources.items():
            keys = []
            repos = []

            for value in values:
                if value.startswith('deb ') or value.startswith('deb-src '):
                    repos.append(value)
                else:
                    keys.append(value)

            if repos:
                with self.write(f'/etc/apt/sources.list.d/{name}.list', False) as fp:
                    for repo in repos:
                        fp.write(repo + '\n')

            if keys:
                with self.prepare(f'/etc/apt/trusted.gpg.d/{name}.gpg', 0o644) as keyring:
                    base = ('apt-key', '--keyring', keyring)

                    for key in keys:
                        if key.startswith('http://') or key.startswith('https://'):
                            run(base + ('adv', '--import'), input=requests.get(key).content)
                        elif key.startswith('hkp://'):
                            server, kid = key.rsplit('/', 1)
                            run(base + ('adv', '--keyserver', server, '--recv', kid))
                        else:
                            run(base + ('adv', '--import'), input=key, text=True)

                Path(str(keyring) + '~').unlink()


class Triggers(BaseModule):
    def _parse_debian_yml_1(self, _, triggers):
        with open(self.target / 'DEBIAN' / 'triggers', 'w') as fp:
            for trigger in triggers:
                fp.write(f'interest {trigger}\n')


class SystemdUnits0(BaseModule):
    def manage(self, unit):
        self.scripts.install(cleandoc(f"""
            systemctl enable "{unit}"
            if ! systemctl cat "{unit}" | grep -xq 'RefuseManualStart=true'; then
                if ! systemctl cat "{unit}" | grep -xq 'RefuseManualStop=true'; then
                    systemctl restart "{unit}"
                else
                    systemctl start "{unit}"
                fi
            fi
        """), cleandoc(f"""
            if ! systemctl cat "{unit}" | grep -xq 'RefuseManualStop=true'; then
                systemctl stop "{unit}"
            fi
            if systemctl is-failed "{unit}" > /dev/null; then
                systemctl reset-failed "{unit}"
            fi
            systemctl disable "{unit}"
        """), 'after', True)


class SystemdUnits1(SystemdUnits0):
    def on_file_write(self, remote, local):
        if remote.parent == PurePath('/lib/systemd/system/'):
            self.scripts.trigger('systemctl daemon-reload', True)

            if local.exists():
                with open(local, 'r') as fp:
                    for line in fp:
                        if '[Install]' in line:
                            self.manage(remote.name)
                            break


class SystemdUnits2(SystemdUnits0):
    def _parse_debian_yml_1(self, _, reload):
        for unit in reload:
            self.systemd_reload(unit)

    def _parse_debian_yml_2(self, _, manages):
        for unit in manages:
            self.manage(unit)


class ManageDBs(BaseModule):
    def _parse_debian_yml_1(self, _, databases):
        for database in databases:
            database['secret'] = self.token(32)

            if database['type'] == 'mysql':
                self.scripts.install(cleandoc("""
                    mysql << EOF
                        CREATE DATABASE IF NOT EXISTS {name};
                        CREATE USER IF NOT EXISTS {name} IDENTIFIED BY '{secret}';
                        GRANT ALL PRIVILEGES ON {name}.* TO {name};
                        ALTER USER {name} IDENTIFIED BY '{password}';
                    EOF
                """.format(**database)), cleandoc("""
                    mysql << EOF
                        ALTER USER {name} IDENTIFIED BY '{secret}';
                    EOF
                """.format(**database)))
                self.scripts.purge(cleandoc("""
                    mysql << EOF
                        DROP DATABASE {name};
                        DROP USER {name};
                    EOF
                """.format(**database)))

            if database['type'] == 'psql':
                self.scripts.install(cleandoc("""
                    sudo -u postgres psql << EOF
                        CREATE DATABASE {name};
                        CREATE USER {name} WITH PASSWORD '{secret}';
                        ALTER DATABASE {name} OWNER TO {name};
                        ALTER USER {name} WITH PASSWORD '{password}';
                    EOF
                """.format(**database)), cleandoc("""
                    sudo -u postgres psql << EOF
                        ALTER USER {name} WITH PASSWORD '{secret}';
                    EOF
                """.format(**database)))
                self.scripts.purge(cleandoc("""
                    sudo -u postgres psql << EOF
                        DROP DATABASE {name} WITH (FORCE);
                        DROP USER {name};
                    EOF
                """.format(**database)))
                if 'extensions' in database:
                    install = ['sudo -u postgres psql ' + database['name'] + ' << EOF', 'EOF']
                    for extension in database['extensions']:
                        install.insert(-1, f'    CREATE EXTENSION IF NOT EXISTS {extension};')
                    self.scripts.install('\n'.join(install), False)

            if database['type'] == 'mongosh':
                self.scripts.install(cleandoc("""
                    mongosh << EOF
                        use {name};
                        if (!db.getUser('{name}')) {{
                            db.createUser({{
                                user: '{name}',
                                pwd: '{secret}',
                                roles: [],
                            }});
                        }}
                        db.updateUser('{name}', {{
                            pwd: '{password}',
                            roles: [
                                {{db: 'local', role: 'read'}},
                                {{db: '{name}', role: 'readWrite'}},
                            ],
                        }});
                    EOF
                """.format(**database)), cleandoc("""
                    mongosh << EOF
                        use {name};
                        db.updateUser('{name}', {{
                            pwd: '{secret}',
                        }});
                    EOF
                """.format(**database)))
                self.scripts.purge(cleandoc("""
                    mongosh << EOF
                        use {name};
                        db.dropAllUsers();
                        db.dropDatabase();
                    EOF
                """.format(**database)))


class CompressGzip(BaseModule):
    def __init__(self, source, target):
        super().__init__(source, target)
        self.compress = set()

    def _parse_debian_yml_1(self, _, compress):
        self.compress = set(compress)

    def process_file(self, path, fp):
        if str(path) in self.compress:
            with self.prepare(path.parent / (path.name + '.gz'), 0o644, path) as output:
                with gzip.open(output, 'wb') as gz:
                    copyfileobj(fp, gz)


class SystemUsers(BaseModule):
    def _parse_debian_yml_1(self, _, users):
        for user in users:
            user.setdefault('home', '/dev/null')

            self.scripts.install(
                'adduser --system --group {name} --home "{home}"'.format(**user),
                'deluser {name}'.format(**user),
                'before',
            )

            if user['home'] != '/dev/null':
                self.scripts.purge('rm -r "{home}"'.format(**user))


class AutoDiversions(BaseModule):
    def on_file_write(self, remote, local):
        self.scripts.install(cleandoc(f"""
            if [[ -f "{remote}" ]] || [[ -f "{remote}.dpkg-new" ]] || [[ -f "{remote}.ucf-dist" ]]; then
                dpkg-divert --rename --divert "{remote}.ucf-dist" --add "{remote}"
            fi
        """), cleandoc(f"""
            dpkg-divert --quiet --rename --divert "{remote}.ucf-dist" --remove "{remote}"
        """), 'before')


class ApplyPatches(BaseModule):
    def on_file_write(self, remote, local):
        if remote.suffix == '.patch':
            original = remote.parent / remote.stem

            self.scripts.install(cleandoc(f"""
                dpkg-divert --rename --divert "{original}.ucf-dist" --add "{original}"
                cp -a "{original}.ucf-dist" "{original}"
                patch --forward "{original}" "{remote}"
                take-control-of "{self.source.name}" "{original}"
            """), False, 'after')

            self.scripts.remove(
                f'dpkg-divert --rename --divert "{original}.ucf-dist" --remove "{original}"',
                False, 'after',
            )


class DockerContainers(BaseModule):
    def _parse_debian_yml_1(self, _, docker):
        for container in docker:
            setup = 'docker run --restart unless-stopped --add-host host.docker.internal:host-gateway'
            purge = 'docker volume rm'

            if 'build' in container:
                dockerfile = PurePath(container['build'])
                image = container['image'] = slugify(str(dockerfile))
                build = f'docker build --tag "{image}" --file "{dockerfile}" "{dockerfile.parent}"'
                setup = build + '\n' + setup

            container.setdefault('sockets', False)
            if container['sockets']:
                setup += ' --volume /run/mysqld/mysqld.sock:/run/mariadb.sock'
                setup += ' --volume /run/redis/redis-server.sock:/run/redis.sock'

            if 'mounts' in container:
                for definition in container['mounts']:
                    setup += f' --volume "{definition}"'

            if 'volumes' in container:
                for definition in container['volumes']:
                    definition = container['name'] + '_' + definition
                    setup += f' --volume "{definition}"'
                    purge += ' "' + definition.split(':')[0] + '"'

            if 'environment' in container:
                for key, value in container['environment'].items():
                    setup += f' --env "{key}={value}"'

            if 'network' in container:
                setup += ' --network "' + container['network'] + '"'

            if 'ports' in container:
                for definition in container['ports']:
                    setup += f' --publish "{definition}"'

            setup += ' --name "' + container['name'] + '"'
            setup += ' --detach "' + container['image'] + '"'

            if 'commands' in container:
                for command in container['commands']:
                    setup += '\ndocker exec "' + container['name'] + '" ' + command

            remove = cleandoc("""
                docker container stop "{name}"
                docker container rm --volumes "{name}"
            """.format(**container))

            if 'volumes' in container:
                self.scripts.purge(purge)
            self.scripts.install(setup, remove, 'after')

            with self.write('/lib/systemd/system/docker-' + container['name'] + '-rebuild.timer', False) as fp:
                fp.write(cleandoc("""
                    [Unit]
                    Description=rebuild of "{name}" Docker container
                    [Timer]
                    OnCalendar=*-*-* 05:00:00
                    Persistent=true
                    RandomizedDelaySec=60m
                    [Install]
                    WantedBy=timers.target
                """.format(**container)))

            with self.write('/lib/systemd/system/docker-' + container['name'] + '-rebuild.service', False) as fp:
                fp.write(cleandoc("""
                    [Unit]
                    Description=rebuild of "{name}" Docker container
                    [Service]
                    Type=oneshot
                """.format(**container)) + '\n')
                for command in remove.split('\n') + setup.split('\n'):
                    fp.write('ExecStart=' + command + '\n')


class GitRepo(BaseModule):
    def _parse_git_yml_1(self, path, url, branch, user, pre, post):
        self.control['pre-depends'] = ['git']

        if user is None:
            user = 'root'
        slug = slugify(str(path.parent))

        if pre is not None:
            with self.write(path.parent / '.git' / 'hooks' / 'pre-pull', True) as fp:
                fp.write(pre)
        if post is not None:
            with self.write(path.parent / '.git' / 'hooks' / 'post-pull', True) as fp:
                fp.write(post)

        self.scripts.install(cleandoc(f"""
            mkdir -p "{path.parent}"
            chown {user}:{user} "{path.parent}"
            cd "{path.parent}"
            sudo -u {user} git init
            sudo -u {user} git remote add origin "{url}"
            sudo -u {user} git fetch
            sudo -u {user} git checkout "{branch}"
            sudo -u {user} git submodule update --init --recursive
        """), f'remove-managed-repo "{path.parent}"', 'before', True)
        self.scripts.purge(f'rm -r "{path.parent}"')

        with self.write(f'/lib/systemd/system/{slug}.timer', False) as fp:
            fp.write(cleandoc(f"""
                [Unit]
                Description=pulling and processing git repo at "{path.parent}"
                [Timer]
                OnCalendar=daily
                Persistent=true
                RandomizedDelaySec=1h
                [Install]
                WantedBy=timers.target
            """))

        with self.write(f'/lib/systemd/system/{slug}.service', False) as fp:
            fp.write(cleandoc(f"""
                [Unit]
                Description=pulling and processing git repo at "{path.parent}"
                [Service]
                Type=oneshot
                User={user}
                WorkingDirectory={path.parent}
                ExecStart=/usr/sbin/update-managed-repo
            """))

        self.scripts.install(f'systemctl start {slug}.service', False, 'after')


class MuninPlugins(BaseModule):
    def on_file_write(self, remote, local):
        if remote.parent == PurePath('/usr/share/munin/plugins/'):
            self.scripts.trigger('systemctl try-restart munin-node', False)

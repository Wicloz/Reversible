class Scripts:
    def __init__(self):
        self.scripts_early = {
            'preinst': [],
            'postinst': [],
            'prerm': [],
            'postrm': [],
        }
        self.scripts_late = {
            'preinst': [],
            'postinst': [],
            'prerm': [],
            'postrm': [],
        }
        self.triggers_early = []
        self.triggers_late = []
        self.purges = []

    def trigger(self, script, internal):
        if internal:
            self.triggers_early.append(script)
        else:
            self.triggers_late.append(script)

    def purge(self, script):
        self.purges.append(script)

    def install(self, script, undo, when='before', late=False):
        scripts = self.scripts_late if late else self.scripts_early
        if when == 'before':
            scripts['preinst'].append(script)
            if undo:
                scripts['postrm'].append(undo)
        if when == 'after':
            scripts['postinst'].append(script)
            if undo:
                scripts['prerm'].append(undo)

    def remove(self, script, undo, when='before', late=False):
        scripts = self.scripts_late if late else self.scripts_early
        if when == 'before':
            scripts['prerm'].append(script)
            if undo:
                scripts['postinst'].append(undo)
        if when == 'after':
            scripts['postrm'].append(script)
            if undo:
                scripts['preinst'].append(undo)

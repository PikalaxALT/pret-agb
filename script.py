# coding: utf-8
"""Classes for parsing Pokemon Emerald scripts.
"""

paths_to_search = ['asm/emerald.s']

labels = {}

def find_labels(path):
	lines = open(path).readlines()
	for line in lines:
		if '.include' in line:
			incpath = line.split('"')[1]
			find_labels(incpath)
		elif ': ;' in line:
			i = line.find(':')
			label, address = line[:i], int(line[i+3:], 16)
			labels[address] = label

for path in paths_to_search:
	find_labels(path)

def load_rom(filename):
    return bytearray(open(filename).read())

baserom = load_rom('base_emerald.gba')


def read_map_groups():
	path = 'constants/map_constants.s'
	lines = open(path).readlines()
	variables = {}
	maps = {}
	for line in lines:
		line = line.strip()
		if line.startswith('.set'):
			name, value = line.split('.set')[1].split(',')
			variables[name.strip()] = int(value, 0)
		elif line.startswith('new_map_group'):
			variables['cur_map_group'] += 1
			variables['cur_map_num'] = 0
			maps[variables['cur_map_group']] = {}
		elif line.startswith('map_group'):
			text = line.split('map_group')[1]
			group, num = map(variables.get, ['cur_map_group','cur_map_num'])
			name = text.split()[0].title().replace('_','')
			maps[group][num] = name
			variables['cur_map_num'] += 1
	return maps

map_groups = read_map_groups()

def read_constants(path):
	lines = open(path).readlines()
	variables = {}
	for line in lines:
		line = line.strip()
		if line.startswith('.set'):
			name, value = line.split('.set')[1].split(',')
			variables[name.strip()] = int(value, 0)
	return {v:k for k,v in variables.items()}

pokemon_constants = read_constants('constants/species_constants.s')
item_constants = read_constants('constants/item_constants.s')

class Object(object):
    arg_names = []
    rom = baserom
    def __init__(self, *args, **kwargs):
        map(self.__dict__.__setitem__, self.arg_names, args)
        self.__dict__.update(kwargs)
        self.parse()
    def parse(self):
        pass

class Chunk(Object):
    arg_names = ['address']
    atomic = False
    @property
    def length(self):
        return self.last_address - self.address
    def parse(self):
        self.pointers = []
        self.chunks = []
        self.last_address = self.address
    def to_asm(self):
        return None

class Param(Chunk):
    num_bytes = 1
    atomic = True
    @property
    def asm(self):
        return str(self.value)
    def to_asm(self):
        return '\t' + self.name + ' ' + self.asm

class Value(Param):
    big_endian = False
    def parse(self):
        Param.parse(self)
        # Note: the loop is to make sure reads are within the bounds of the rom.
        bytes_ = []
        for i in xrange(self.num_bytes):
            bytes_ += [self.rom[self.address + i]]
        #bytes_ = self.rom[self.address : self.address + self.num_bytes]
        if self.big_endian:
            bytes_.reverse()
        self.value = sum(byte << (8 * i) for i, byte in enumerate(bytes_))
        self.last_address = self.address + self.num_bytes

class Byte(Value):
    name = '.byte'
    num_bytes = 1

class Word(Value):
    name = '.2byte'
    num_bytes = 2

class Int(Value):
    name = '.4byte'
    num_bytes = 4
    @property
    def asm(self):
        return '0x{:x}'.format(self.value)

def is_rom_address(address):
    return (0x8000000 <= address <= 0x9ffffff)

class Pointer(Int):
    target = None
    target_arg_names = []
    include_address = True # passed to Label

    def resolve(self):
        if not is_rom_address(self.value):
            return None
        if self.target is None:
            return None
        return self.target(self.real_address, **self.target_args)
    @property
    def target_args(self):
        return { k: getattr(self, k, None) for k in self.target_arg_names }
    def get_label(self):
        if hasattr(self, 'label'):
            return self.label.asm
        return labels.get(self.value)
    @property
    def real_address(self):
        if not is_rom_address(self.value) and not self.value == 0:
            #raise Exception('invalid pointer at 0x{:08x} (0x{:08x})'.format(self.address, self.value))
            return None
        return self.value & 0x1ffffff
    @property
    def asm(self):
        label = self.get_label()
        if label:
            return label
        return '0x{:x}'.format(self.value)

class ThumbPointer(Pointer):
	def get_label(self):
		return Pointer.get_label(self) or labels.get(self.value - 1)

class ParamGroup(Chunk):
    param_classes = []
    def parse(self):
        Chunk.parse(self)
        address = self.address
        self.chunks = []
        self.params = {}
        for item in self.param_classes:
            name = None
            try:
                name, param_class = item
            except:
                param_class = item
            param = param_class(address)
            self.chunks += [param]
            if name:
                self.params[name] = param
            address += param.length
        self.last_address = address
    @property
    def asm(self):
        return ', '.join(param.asm for param in self.chunks)

class Variable(Word):
    @property
    def asm(self):
        return '0x{:x}'.format(self.value)

class WordOrVariable(Word):
    @property
    def asm(self):
        if self.value >= 0x4000:
            return '0x{:x}'.format(self.value)
        return str(self.value)

class Species(Word):
	@property
	def asm(self):
		return pokemon_constants.get(self.value, str(self.value))
class Item(Word):
	@property
	def asm(self):
		return item_constants.get(self.value, str(self.value))

class Macro(ParamGroup):
    atomic = True
    def to_asm(self):
	chunks = self.chunks
        return '\t' + self.name + ' ' + ', '.join(param.asm for param in chunks)

class Command(Macro):
    end = False
    def to_asm(self):
        chunks = self.chunks[1:]
        return '\t' + self.name + ' ' + ', '.join(param.asm for param in chunks)

class Label(Chunk):
    atomic = True
    context_label = 'g'
    default_label_base = 'Unknown'
    include_address = True
    address_comment = True
    def parse(self):
        Chunk.parse(self)
        if not hasattr(self, 'asm'):
            label = self.context_label + self.default_label_base
            if self.include_address:
                label += '_0x{:x}'.format(self.address)
            self.asm = label
    def to_asm(self):
        asm = self.asm + ':'
        if self.address_comment:
            asm += ' ; 0x{:x}'.format(self.address)
        return asm
    #@property
    #def label(self):
    #    return get_label(self.address)

class Script(Chunk):
    commands = {}
    default_label = Label
    def parse(self):
        Chunk.parse(self)
        self.chunks = []
        address = self.address
        end = False
        while not end:
            byte = Byte(address)
            command_class = self.commands.get(byte.value)
            if command_class:
                command = command_class(address)
                self.chunks += [command]
                end = command.end
                address += command.length
            else:
                break
        self.last_address = address
        #self.chunks += [self.get_label()]
    def get_label(self):
        return self.default_label(self.address)
    def to_asm(self):
        return print_chunks(self.chunks)


class MapId(Macro):
	name = 'map'
	param_classes = [
		('group', Byte),
		('number', Byte),
	]
	@property
	def asm(self):
		group = self.params['group'].value
		number = self.params['number'].value
		map_name = map_groups.get(group, {}).get(number)
		if not map_name:
			return Word(self.address).asm
		return map_name
	def to_asm(self):
		return '\t' + 'map ' + self.asm

class WarpMapId(MapId):
	"""Reversed MapId."""
	param_classes = [
		('number', Byte),
		('group', Byte),
	]


def print_chunks(chunks):
    def is_label(asm):
        if asm:
            line = asm.split(';')[0].rstrip()
            if line and line[-1] == ':':
                return True
        return False
    sorted_chunks = sorted(set((c.address, c.last_address, c.to_asm()) for c in chunks))
    lines = []
    previous_address = None
    for address, last_address, asm in sorted_chunks:
        if previous_address:
            if address > previous_address:
                # awful hack to catch unnecessary ends
                if address - previous_address == 1 and baserom[previous_address] == 0x2:
                    lines += ['\tend']
                else:
                    if lines and not is_label(lines[-1]):
                        lines += ['']
                    lines += ['\tbaserom 0x{:x}, 0x{:x}'.format(previous_address, address), '']
            elif address < previous_address:
                if asm: asm = ';' + asm
                #lines += ['; ERROR (0x{:x}, 0x{:x})'.format(address, previous_address)]
	if asm:
            if lines and lines[-1]:
                if is_label(asm) and not is_label(lines[-1]):
                    lines += ['']
            lines += [asm]
        previous_address = last_address
    return ('\n'.join(lines) + '\n').encode('utf-8')

def print_scripts(scripts):
    chunks = []
    for script in scripts:
        chunks += script.chunks
    return print_chunks(chunks)

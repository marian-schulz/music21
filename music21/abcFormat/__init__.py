# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
# Name:         abc/__init__.py
# Purpose:      parses ABC Notation
#
# Authors:      Christopher Ariza
#               Dylan J. Nagler
#               Michael Scott Cuthbert
#               Marian Schulz
#
# Copyright:    Copyright © 2010, 2013 Michael Scott Cuthbert and the music21 Project
# License:      BSD, see license.txt
# ------------------------------------------------------------------------------
'''
ABC is a music format that, while being able to encode all sorts of scores, is especially
strong at representing monophonic music, and folk music in particular.

Modules in the `music21.abcFormat` package deal with importing ABC into music21.  Most people
working with ABC data won't need to use this package.  To convert ABC from a file or URL
to a :class:`~music21.stream.Stream` use the :func:`~music21.converter.parse` function of
the `converter` module:

>>> #_DOCS_SHOW from music21 import *
>>> #_DOCS_SHOW abcScore = converter.parse('/users/ariza/myScore.abc')

For users who will be editing ABC extensively or need a way to have music21 output ABC
(which it doesn't do natively), we suggest using the open source EasyABC package:
http://www.nilsliberg.se/ksp/easyabc/ .  You can set it up as a MusicXML reader through:

>>> #_DOCS_SHOW us = environment.UserSettings()
>>> #_DOCS_SHOW us['musicxmlPath'] = '/Applications/EasyABC.app'

or wherever you have downloaded EasyABC to
(PC users might need: 'c:/program files (x86)/easyabc/easyabc.exe')
(Thanks to Norman Schmidt for the heads up)

There is a two-step process in converting ABC files to Music21 Streams.  First this module
reads in the text-based .abc file and converts all the information into ABCToken objects.  Then
the function :func:`music21.abcFormat.translate.abcToStreamScore` of
the :ref:`moduleAbcFormatTranslate` module
translates those Tokens into music21 objects.
'''
__all__ = [
    'translate',
    'testFiles',
    'ABCTokenException', 'ABCHandlerException', 'ABCFileException',
    'ABCToken', 'ABCArticulation', 'ABCExpression', 'ABCSpanner',
    'ABCMetadata', 'ABCBar', 'ABCTuplet', 'ABCTie',
    'ABCSlurStart', 'ABCParenStop', 'ABCCrescStart', 'ABCDimStart',
    'ABCGraceStart', 'ABCGraceStop', 'ABCBrokenRhythm',
    'ABCNote', 'ABCChord', 'ABCGeneralNote', 'ABCRest',
    'ABCHandler', 'ABCHandlerBar', 'ABCHandlerVoice', 'ABCVoiceProcessor',
    'ABCFile',
]

import copy
import io
import pathlib
import re
import unittest
from typing import Union, Optional, List, Tuple, Type, Dict, Callable, Iterator, NamedTuple
import itertools

from music21 import common
from music21 import environment
from music21 import exceptions21
from music21 import prebase
from music21 import articulations
from music21 import expressions
from music21 import dynamics
from music21 import repeat
from music21 import meter
from music21 import clef
from music21 import spanner
from music21.abcFormat import translate
from music21.abcFormat import testFiles
from music21.abcFormat.accents import encodeAccentsAndLigatures

# for implementation
# see http://abcnotation.com/abc2mtex/abc.txt
# AND http://abcnotation.com/wiki/abc:standard:v2.1

environLocal = environment.Environment('abcFormat')

# Misc constants
METADATA_TEXT_TYPE_FIELDS = 'ABCDCGHNORSTWwZ'
METADATA_INLINE_FIELDS = 'IKLMmNPQRrUV'

# store symbol and m21 naming/class eq
ABC_BARS = [
    (':|1', 'light-heavy-repeat-end-first'),
    (':|2', 'light-heavy-repeat-end-second'),
    ('|]', 'light-heavy'),
    ('||', 'light-light'),
    ('[|', 'heavy-light'),
    ('[1', 'regular-first'),  # preferred format
    ('[2', 'regular-second'),
    ('|1', 'regular-first'),  # gets converted
    ('|2', 'regular-second'),
    (':|', 'light-heavy-repeat-end'),
    ('|:', 'heavy-light-repeat-start'),
    ('::', 'heavy-heavy-repeat-bidirectional'),
    # for comparison, single chars must go last
    ('|', 'regular'),
    (':', 'dotted'),
]
# Regular Expressions
RE_ABC_NOTE = re.compile(r'([\^_=]*)([A-Ga-gz])([0-9/\',]*)')
RE_ABC_VERSION = re.compile(r'(?:((^[^%].*)?[\n])*%abc-)(\d+)\.(\d+)\.?(\d+)?')
RE_ABC_LYRICS = re.compile(r'[*\-_|]|[^*\-|_ ]+[\-]?')

# ABC keysignature I: K:<tonic> <mode> <accidentals>
RE_ABC_KEY_MODE = re.compile(r'(?P<tonic>H[pP]|[A-G][#b]?)?\s*'+
                             r'(?P<mode>[a-zA-Z]*)(?P<accidentals>\s*.*)')

# ABC keysignature II: K:<tonic> exp <accidentals>
RE_KEY_EXP = re.compile(r'(?P<tonic>(H[pP])' +
        r'|([A-G]?[#b]?))\s+exp\s+(?P<accidentals>.*)')

# ABC Clef sytntax
# @TODO: Multiline - realy ?
CLEF_RE = re.compile(r'(?P<clef_name>clef\s*=\s*\S+(?!\S))' +
        r'|(?P<octave>octave=[+\-]?[0-9](?!\S))' +
        r'|(?P<transpose>t(ranspose)?\s*=\s*[+\-]?[0-9]+(?!\S))' +
        r'|(?P<unamed>[^=]+?(?!\S))', re.MULTILINE)

# ABC VOICE syntax
VOICE_RE = re.compile(r'(?P<id>^\S+)|(?P<name>(name|nm)\s*=\s*(".*?"|\S+)(?!\S))' +
                      r'|(?P<subname>(subname|snm|sname)\s*=\s*(".*?"|\S+)(?!\S))')

# store a mapping of ABC representation to pitch values
_pitchTranslationCache = {}

def makeMetaDataRegexpr(tag: str):
    """
    Creates a regular expression for ABCMetadata token class.
    """
    tag = tag[0]

    if tag in METADATA_TEXT_TYPE_FIELDS:
        regex = [
            fr'^\s*{tag}:.*(([\\]\n\s*{tag}:[^\n\\]+)|' +
            r'([\\]?\n\s*[+]:([^\n\\]|[\\](?!\n))+))*$']
    else:
        regex = [rf'^\s*{tag}:.*']

    if tag in METADATA_INLINE_FIELDS:
        regex.append(rf'\[{tag}:[^\]\n%]*\]')

    return "|".join(regex)


# Type aliases
ABCVersion = Tuple[int, int, int]


# ------------------------------------------------------------------------------
class ABCTokenException(exceptions21.Music21Exception):
    pass


class ABCHandlerException(exceptions21.Music21Exception):
    pass


class ABCFileException(exceptions21.Music21Exception):
    pass


# ------------------------------------------------------------------------------
class ABCToken(prebase.ProtoM21Object):
    '''
    ABC processing works with a multi-pass procedure. The first pass
    breaks the data stream into a list of ABCToken objects. ABCToken
    objects are specialized in subclasses.

    The multi-pass procedure is conducted by an ABCHandler object.
    The ABCHandler.tokenize() method breaks the data stream into
    ABCToken objects. The :meth:`~music21.abcFormat.ABCHandler.tokenProcess` method does
    the contextual adjustments to all tokens.

    the token classes derived from ABCToken can define a regular expression in the property
    'TOKEN_REGEX' via which they can be recognised by the tokenizer.

    The source ABC string itself is stored in self.src
    '''

    # Define a regular expression in the subclasses for the tokenizer
    TOKEN_REGEX = None

    def __init__(self, src=''):
        self.src: str = src  # store source character sequence

    def _reprInternal(self):
        return repr(self.src)

    def m21Object(self):
        return None


class ABCTune(NamedTuple):
    header: 'ABCHandler'
    voices: List['ABCHandlerVoice']


class ABCMark(ABCToken):
    '''
    Base class of abc score marker token
    Marker can placed on every position in a stream.
    '''
    def __init__(self, src: str, m21Class: Type):
        super().__init__(src)
        self._m21Object = m21Class()

    def m21Object(self):
        return self._m21Object


class ABCDynamic(ABCMark):
    def __init__(self, src):
        super().__init__(src, dynamics.Dynamic)
        self._m21Object.value = self.src[1:-1]


class ABCSpanner(ABCToken):
    """
    Defines a base class for all spanner type tokens
    """
    def __init__(self, src, m21Class: Type):
        super().__init__(src)
        self._m21Object = m21Class()

    def m21Object(self):
        return self._m21Object


class ABCArticulation(ABCToken):
    def __init__(self: str, src, m21Class: Type):
        super().__init__(src)
        self._m21Object = m21Class()

    def m21Object(self):
        return self._m21Object


class ABCExpression(ABCToken):
    def __init__(self: str, src, m21Class: Type):
        super().__init__(src)
        self._m21Object = m21Class()

    def m21Object(self):
        return self._m21Object


class ABCAnnotations(ABCMark):
    """
    ABC text Annotations are set in quotation marks,
    the first charakter indicate where the annotation has
    set relative to the next note
    ^ : above the note
    _ : below the note
    < : left of the note
    > : right of the note
    @ : indicate a free placement position
    """
    TOKEN_REGEX = '"[\^_<>@][^"]*"'

    def __init__(self, src: str):
        super().__init__(src, expressions.TextExpression)
        src = src.strip('"')
        self._m21Object.content = src[1:]
        if src[0] == '^':
            self._m21Object.positionPlacement = 'above'
        elif src[0] == '_':
            self._m21Object.positionPlacement = 'below'


class ABCCrescStart(ABCSpanner):
    '''
    ABCCrescStart tokens always precede the notes in a crescendo.
    These tokens coincide with the string "!crescendo(";
    the closing string "!crescendo)" counts as an ABCParenStop.
    '''

    def __init__(self, src):
        super().__init__(src, dynamics.Crescendo)


class ABCDimStart(ABCSpanner):
    '''
    ABCDimStart tokens always precede the notes in a diminuendo.
    They function identically to ABCCrescStart tokens.
    '''

    def __init__(self, src):
        super().__init__(src, dynamics.Diminuendo)


class ABCParenStop(ABCToken):
    TOKEN_REGEX = r'\)'


class ABCFermata(ABCExpression):
    '''
    The ABC Fermata is the 'upright' fermata in music21
    '''
    def __init__(self, src):
        super().__init__(src, expressions.Fermata)
        self._m21Object.type ='upright'


M21_DECORATIONS = {
    'crescendo(': ABCCrescStart,
    '<(': ABCCrescStart,
    'crescendo)': ABCParenStop,
    '<)': ABCParenStop,
    'diminuendo(': ABCDimStart,
    '>(': ABCDimStart,
    'diminuendo)': ABCParenStop,
    '>)': ABCParenStop,
    'staccato': articulations.Staccato,
    'downbow': articulations.DownBow,
    'uppermordent': expressions.InvertedMordent,
    'pralltriller': expressions.InvertedMordent,
    'lowermordent': expressions.Mordent,
    'mordent': expressions.Mordent,
    'upbow': articulations.UpBow,
    'emphasis': articulations.Accent,
    'accent': articulations.Accent,
    'straccent': articulations.StrongAccent,
    'tenuto': articulations.Tenuto,
    'fermata': ABCFermata,
    'invertedfermata': expressions.Fermata,
    'trill': expressions.Trill,
    'coda': repeat.Coda,
    #'turn': expressions.Turn,
    'segno': repeat.Segno,
    'snap': articulations.SnapPizzicato,
    '.': articulations.Staccato,
    '>': articulations.Accent,
    'D.S.': ABCAnnotations('_D.S.'),
    'D.C.': ABCAnnotations('_D.C.'),
    'dacapo': ABCAnnotations('^DA CAPO'),
    'fine': ABCAnnotations('^FINE')
    # 'arpeggio'              vertical squiggle
    # '^':                    marcato (inverted V)
}

def ABCDecoration(src):
    """
    ABCDecoration is a factory function for ABCArticulation & ABCExpression
    >>> abcFormat.ABCDecoration("!trill!")
    <music21.abcFormat.ABCExpression '!trill!'>
    >>> abcFormat.ABCDecoration("!tenuto!")
    <music21.abcFormat.ABCArticulation '!tenuto!'>
    >>> abcFormat.ABCDecoration("!ppp!")
    <music21.abcFormat.ABCDynamic '!ppp!'>
    """
    deco_key = src.strip('!').strip('+')
    try:
        decoration_class = M21_DECORATIONS[deco_key]
        if isinstance(decoration_class, ABCAnnotations):
            return decoration_class
        if issubclass(decoration_class, articulations.Articulation):
            return ABCArticulation(src, decoration_class)
        elif issubclass(decoration_class, expressions.Expression):
            return ABCExpression(src, decoration_class)
        elif issubclass(decoration_class, repeat.RepeatMark):
            return ABCMark(src, decoration_class)
        elif issubclass(decoration_class, ABCToken):
            return decoration_class(src)

        raise ABCTokenException(f'Unknown type "{decoration_class}" for decoration "{src}"')

    except KeyError:
        if deco_key in "12345":
            return ABCArticulation(deco_key, articulations.Fingering)
        if deco_key in ['p', 'pp', 'ppp', 'pppp', 'f', 'ff', 'fff',
                       'ffff', 'mp', 'mf', 'sfz']:
            return ABCDynamic(src)
        raise ABCTokenException(f'Unknown abc decoration "{src}"')


class ABCMetadata(ABCToken):

    # Not evaluated ABC Fields.
    TOKEN_REGEX = '^[BADFGHZmNPrRSWZ]:.*'

    def __init__(self, src):
        super().__init__(src.strip())

        # Inline fields are enclosed by brackets [K: C]
        if src.startswith('['):
            self.inlined = True
            src = src[1:-1]
        else:
            self.inlined = False

        # split tag and trailing data
        src = src.split(':', 1)
        self.tag: str = src[0].strip()
        data = src[1].strip()

        # special treatment for text fields (if not inlined)
        if not self.inlined and self.tag in METADATA_TEXT_TYPE_FIELDS:
            # Text fields can contain accents and ligatures
            data = encodeAccentsAndLigatures(data)

            # Text fields can extend over several lines.
            # Either by '+:' on the next line or by an '\' on the
            # end of the line. (Only if the next line has the same tag).
            lines = data.split('\n')
            # for the first line the tag is already speperated
            _data = [lines[0].rstrip(r'\\').strip()]
            for line in lines[1:]:
                # remove leading tag
                line = line.split(':', 1)[1]
                # remove line continuation charakter
                # and leading white spaces
                line = line.rstrip(r'\\').lstrip()
                # remove comments in each line and trailing whitespace
                line = line.split('%', 1)[0].strip()
                _data.append(line)

            # join the line to one line seperated by a whitespace
            data = " ".join(_data)
        else:
            # remove comments and trailing whitespace
            data = src[1].split('%', 1)[0].strip()

        self.data: str = data


class ABCReferenceNumber(ABCMetadata):
    TOKEN_REGEX = makeMetaDataRegexpr('X')
    def __init__(self, src):
        super().__init__(src)
        x, _ = common.getNumFromStr(self.data)
        self.data = int(self.data)


class ABCTitle(ABCMetadata):
    TOKEN_REGEX = makeMetaDataRegexpr('T')


class ABCOrigin(ABCMetadata):
    TOKEN_REGEX = makeMetaDataRegexpr('O')


class ABCComposer(ABCMetadata):
    TOKEN_REGEX = makeMetaDataRegexpr('C')


class ABCUnitNoteLength(ABCMetadata):
    r"""
    ABCUnitNoteLength represent the ABC filed L:

    >>> am = abcFormat.ABCUnitNoteLength('L:1/2')
    >>> am.defaultQuarterLength
    2.0

    >>> am = abcFormat.ABCUnitNoteLength('L:1/8')
    >>> am.defaultQuarterLength
    0.5
    """
    TOKEN_REGEX = makeMetaDataRegexpr('L')

    def __init__(self, src):
        super().__init__(src)
        if '/' in self.data:
            # should be in L:1/4 form
            n, d = self.data.split('/',1)
            n = int(n.strip())
            # the notation L: 1/G is found in some essen files
            # this is extremely uncommon and might be an error
            if d == 'G':
                d = 4  # assume a default
            else:
                d = int(d.strip())
            # 1/4 is 1, 1/8 is 0.5
            self.defaultQuarterLength = n * 4 / d
        else:
            raise ABCTokenException(f'Invalid form of the unit not length "{self.src}"')


class ABCClef():
    """
    The clefs with the +/-8 at then end transpose the melody an octave
    up or down if no octave modifier has explicitly set.
    When specifying the name, the 'clef=' can also be omitted

    >>> md = abcFormat.ABCClef('clef="treble"').clef_name
    treble

    >>> abcFormat.ABCClef('bass').clef_name
    bass

    >>> md = abcFormat.ABCClef('bass-8')
    >>> md.clef_name
    bass8vb

    >>> md = abcFormat.ABCClef('bass -8va')
    >>> md.clef_name
    bass8va

    >>> md = abcFormat.ABCClef('clef=treble+8 octave=-2 transpose="14"')
    >>> md.clef_name
    treble8va
    >>> md.octave
    -2
    >>> md.transpose
    14
    """

    _NAMES = ['treble', 'treble8vb', 'treble8va', 'bass', 'bass8va',
              'bass8vb', 'mezzosoprano', 'soprano', 'baritone', 'alto',
              'tenor', 'percussion', 'g1', 'g2', 'f3', 'f4', 'f5', 'c1',
              'c2', 'c3', 'c4']

    _ALIAS = {
        'perc': 'percussion',
        'treble-8': 'treble8vb',
        'treble+8': 'treble8va',
        'bass-8': 'bass8vb',
        'bass+8': 'bass8va',
        'alto1': 'soprano',
        'alto2': 'mezzosoprano',
        'bass3': 'baritone',
    }

    def __init__(self, data: str=''):
        self._transpose: Optional[int] = None
        self._octave: Optional[int] = None
        self._clef_name: str = ''
        self._ottava: str = ''

        # list of unamed properties
        unamed  = []
        for m in CLEF_RE.finditer(data):
            k = m.lastgroup
            v = m.group()
            if k in ['transpose', 'octave', 'clef_name']:
                setattr(self, k, v.split('=')[1].lower().strip('"').strip())
            else:
                unamed.append(v.lower().strip())

        # the clef name is allowed without clef=<name>
        if self.clef_name is None:
            for tag in unamed:
                if tag in ABCClef._NAMES:
                    self.clef_name = tag
                elif tag in ABCClef._ALIAS:
                    self.clef_name = ABCClef._ALIAS[tag]

        if self.clef_name in ABCClef._ALIAS:
            self.clef_name = ABCClef._ALIAS[self.clef_name]
        elif self.clef_name not in ABCClef._NAMES:
            if self.clef_name is not None:
                environLocal.printDebug([f'Unknown clef name "{self.clef_name}" in abc source.'])


        # Optional ottava syntax
        # change the octave and for bass & treble the related clefs
        # if no
        if self.clef_name in ['bass', 'treble']:
            if '-8va' in unamed:
                self.clef_name += '8va'
            elif '-8vb' in unamed:
                self.clef_name += '8vb'

        if self.octave is not None:
            self.octave = int(self.octave)

        if self.transpose is not None:
            self.transpose = int(self.transpose)


    def join(self, other: 'ABCClef'):
        '''
           Creates a new clef Object, with properties of this and other clef.
        '''
        clf = ABCClef()
        clf._transpose = self._transpose if other._transpose is None else other._transpose
        clf._octave = self._octave if other._octave is None else other._octave
        clf._name = self._name if other._name is None else other._name
        clf._transposing_clef = ''

    @property
    def transpose(self):
        if self._transpose is None:
            return 0
        return self._transpose

    @property
    def octave(self):
        if self._octave is None:
            return 0
        return self._octave

    @property
    def clef_name(self):
        if self._clef_name:
            return f"{self._clef_name}{self.otava}"
        return 'treble'

class ABCVoice(ABCMetadata,  ABCClef):

    TOKEN_REGEX = makeMetaDataRegexpr('V')

    def __init__(self, src):
        r"""
        The first value (required) of the field is the ID of the voice.
        Following optional properties follow the scheme property=<value>.
        Values can appear with or without enclosing quotation marks.

        The voice field can additionally contain all clef properties (see ABClef)

        ABC voice properties:
            name=<voice name>
                Printed on the left of the first staff.
                The name of the property is sometimes abbreviated as 'nm'
                Corresponds to the music21 property "partName"

            subname=<voice subname>:
                Printed on the left of all staves but the first one.
                The name of the property is sometimes abbreviated as 'snm' or 'sname'
                Corresponds to the music21 property "partAbbreviation"

            stem=<up/down>
                Note stem direction.
                @TODO: not implemented in translator


        >>> v = abcFormat.ABCVoice('V:1 nm="piano" subname=accompaniment stem=down')
        >>> v.voiceId
        '1'
        >>> v.name
        'piano'
        >>> v.subname
        'accompaniment'
        >>> v.stem
        'down'

        We got also clef informations from a voice field via ABCClef
        >>> v = abcFormat.ABCVoice("V:1 treble")
        >>> v.clef
        <music21.clef.TrebleClef>
        """
        super().__init__(src)
        ABCClef.__init__(self, self.data)

        self.voiceId: str = ''
        self.name : Optional[str] = None
        self.subname: Optional[str] = None
        self.stem: Optional[str] = None

        for m in VOICE_RE.finditer(self.data):
            k = m.lastgroup
            v = m.group()
            if k == 'id':
                self.voiceId = v
            elif k in ['name', 'subname', 'stem']:
                setattr(self, k, v.split('=')[1].strip().strip('"'))

        if not self.voiceId:
            raise ABCTokenException('Required voice ID missing in abc voice field.')

        if self.stem is not None:
            self.stem = self.stem.lower()
            if self.stem not in ['up', 'down']:
                environLocal.warn(f'Illegal value "{self.stem}" for the voice property stem (up/down).')
                self.stem = None


class ABCTempo(ABCMetadata):
    """
    >>> am = abcFormat.ABCTempo('Q: "Allegro" 1/4=120')
    >>> am.getMetronomeMarkObject()
    <music21.tempo.MetronomeMark Allegro Quarter=120.0>
    """
    TOKEN_REGEX = makeMetaDataRegexpr('Q')

    def __init__(self, src):
        super().__init__(src)

    def getMetronomeMarkObject(self) -> Optional['music21.tempo.MetronomeMark']:
        '''
        Extract any tempo parameters stored in a tempo metadata token.

        >>> am = abcFormat.ABCTempo('Q: "Allegro" 1/4=120')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark Allegro Quarter=120.0>

        >>> am = abcFormat.ABCTempo('Q: 3/8=50 "Slowly"')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark Slowly Dotted Quarter=50.0>

        >>> am = abcFormat.ABCTempo('Q:1/2=120')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark animato Half=120.0>

        >>> am = abcFormat.ABCTempo('Q:1/4 3/8 1/4 3/8=40')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark grave Whole tied to Quarter (5 total QL)=40.0>

        >>> am = abcFormat.ABCTempo('Q:90')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark maestoso Quarter=90.0>
        '''
        mmObj = None
        from music21 import tempo
        # see if there is a text expression in quotes
        tempoStr = None
        if '"' in self.data:
            tempoStr = []
            nonText = []
            isOpen = False
            for char in self.data:
                if char == '"' and not isOpen:
                    isOpen = True
                    continue
                if char == '"' and isOpen:
                    isOpen = False
                    continue
                if isOpen:
                    tempoStr.append(char)
                else:  # gather all else
                    nonText.append(char)
            tempoStr = ''.join(tempoStr).strip()
            nonText = ''.join(nonText).strip()
        else:
            nonText = self.data.strip()

        # get a symbolic and numerical value if available
        number = None
        referent = None
        if nonText:
            if '=' in nonText:
                durs, number = nonText.split('=')
                durs = durs.strip()
                number = float(number)
                # there may be more than one dur divided by a space
                referent = 0.0  # in quarter lengths
                for dur in durs.split(' '):
                    if dur.count('/') > 0:
                        n, d = dur.split('/')
                    else:  # this is an error case
                        environLocal.printDebug(['incorrectly encoded / unparsable duration:', dur])
                        n, d = '1', '1'
                    # n and d might be strings...
                    referent += (float(n) / float(d)) * 4
            else:  # assume we just have a quarter definition, e.g., Q:90
                number = float(nonText)

        # print(nonText, tempoStr)
        if tempoStr is not None or number is not None:
            mmObj = tempo.MetronomeMark(text=tempoStr, number=number,
                                        referent=referent)
        # returns None if not defined
        mmObj.style.fontStyle = 'italic'

        # Fix to placment of the metronommark & tempotext
        mmObj.positionPlacement = 'above'
        if mmObj._tempoText:
            mmObj._tempoText._textExpression.positionPlacement = 'above'

        return mmObj


class ABCUserDefinition(ABCMetadata):
    r"""
    This is the Token for the ABC filed 'U:'
    The token creates ABCTokens for a userdefined symbol

    >>> ud = abcFormat.ABCUserDefinition('U:m=.')
    >>> ud.symbol
    'm'
    >>> ud.definition
    '.'
    """
    TOKEN_REGEX = makeMetaDataRegexpr('U')

    def __init__(self, src):
        super().__init__(src)

        parts = self.data.split('=', 1)
        self.symbol = parts[0].strip()
        self.definition = parts[1].strip()

    def tokenize(self, parent: Optional['ABCHandler']=None) -> Optional[List[ABCToken]]:
        """
        >>> abcFormat.ABCUserDefinition('U:m=!trill!').tokenize()
        [<music21.abcFormat.ABCExpression '!trill!'>]
        """
        if self.definition in ['!nil!', '!none!']:
            return None

        abcVersion = None if parent is None else parent.abcVersion
        return list(tokenize(self.definition, abcVersion))


class ABCKey(ABCMetadata, ABCClef):
    r"""
    This is the Token for the ABC filed 'K:'
    The ABCKey specified a Key or KeySignature.
    All values were translated into m21 compatible notation.
    The key signature should be specified with a capital letter (A-G) which
    may be followed by a # or b for sharp or flat respectively.
    In addition the mode should be specified (when no mode but a tonic is indicated,
    major is assumed).

    The spaces can be left out, capitalisation is ignored for the modes
    The key signatures may be modified by adding accidentals, according to the
    format: K:<tonic> <mode> <accidentals>.

    However, it can also specify a clef in addition.
    The clef follows after the key and key modifications

    >>> am = abcFormat.ABCKey('K:')
    >>> am.tonic is None
    True
    >>> am.mode is None
    True
    >>> am.alteredPitches
    []
    >>> am.getClefObject() is None
    True

    >>> am = abcFormat.ABCKey('K:Eb Lydian ^e treble')
    >>> am.tonic
    'E-'
    >>> am.mode
    'lydian'
    >>> am.alteredPitches
    ['E#']
    >>> am.getKeySignatureObject()
    <music21.key.KeySignature of pitches: [B-, E#]>
    >>> am.getClefObject()
    <music21.clef.TrebleClef>
    """

    TOKEN_REGEX = makeMetaDataRegexpr('K')

    def __init__(self, src: str):
        super().__init__(src)
        ABCClef.__init__(self, self.data)
        self.tonic: Optional[str] = None
        self.mode: Optional[str] = None
        self.alteredPitches: List[str] = []
        self._setKeySignatureParameters()

    def _setKeySignatureParameters(self):
        """
        Set the ABCKey parameters:
            self.tonic: Optional[str]
            self.mode: Optional[str]
            self.alteredPitches: List[str]
        """

        # abc uses b for flat and # for sharp in key tonic spec only
        TonicNames = {'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'G#', 'A#', 'F',
                      'Bb', 'Eb', 'D#', 'Ab', 'E#', 'Db', 'C#', 'Gb', 'Cb'}

        # ABC accidentals mapped to m21 accidentals
        accidentalMap = {'=': 'n', '_': '-', '__': '--', '^': '#', '^^': '##'}

        modeMap = {'dor': 'dorian', 'phr': 'phrygian', 'lyd': 'lydian',
                   'mix': 'mixolydian', 'maj': 'major', 'ion': 'ionian',
                   'aeo': 'aeolian', 'loc': 'locrian', 'min': 'minor',
                   'm': 'minor'}

        #if self.data == '^c _c':
        #    breakpoint()

        match = RE_KEY_EXP.match(self.data)

        if not match:
            match = RE_ABC_KEY_MODE.match(self.data)
            if match:
                # Major is the default mode if mode is missing
                # Only the first 3 letters of the mode are evaluated
                m = match.groupdict()['mode'][:3].lower()
                self.mode = 'major' if not m else modeMap.get(m, 'major')
            else:
                return

        a = match.groupdict()['accidentals'].strip()
        t = match.groupdict()['tonic']
        accidentals = {}

        if t == 'Hp':
            # Scotish bagpipe tune
            self.tonic = 'D'
            self.mode = None
            accidentals = {'F': '#', 'C': '#'}
        elif t == 'HP':
            self.tonic = 'C'
            self.mode = None
        elif t in TonicNames:
            # replace abc flat(b) with m21 flat(-)
            self.tonic = t.replace('b', '-')
        else:
            # without tonic no valid mode
            self.mode = None

        for accStr in a.split():
            # last char is the note symbol
            note, acc = accStr[-1].upper(), accStr[:-1]
            # the leading chars are accidentals =,^,_
            if acc in accidentalMap and note in 'ABCDEFG':
                accidentals[note] = accidentalMap[acc]

        self.alteredPitches = [f"{n}{a}" for n, a in accidentals.items()]


    def getKeySignatureObject(self) -> 'music21.key.KeySignature':
        # noinspection SpellCheckingInspection,PyShadowingNames
        '''
        Return a music21 :class:`~music21.key.KeySignature` or :class:`~music21.key.Key`
        object for this metadata tag.
        >>> am = abcFormat.ABCKey('K:G =F')
        >>> am.getKeySignatureObject()
        <music21.key.Key of G major>

        >>> am = abcFormat.ABCKey('K:G ^d')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [F#, D#]>

        >>> am = abcFormat.ABCKey('K:G')
        >>> am.getKeySignatureObject()
        <music21.key.Key of G major>

        >>> am = abcFormat.ABCKey('K:Gmin')
        >>> am.getKeySignatureObject()
        <music21.key.Key of g minor>

        >>> am = abcFormat.ABCKey('K:E exp ^c ^a')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [C#, A#]>

        >>> am = abcFormat.ABCKey('K:GM')
        >>> am.getKeySignatureObject()
        <music21.key.Key of g minor>

        >>> am = abcFormat.ABCKey('K:Hp')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [F#, C#]>

        >>> am = abcFormat.ABCKey('K:HP')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of no sharps or flats>

        >>> am = abcFormat.ABCKey('K:C =c')
        >>> am.getKeySignatureObject()
        <music21.key.Key of C major>

        >>> am = abcFormat.ABCKey('K:C ^c =c')
        >>> am.getKeySignatureObject()
        <music21.key.Key of C major>

        >>> am = abcFormat.ABCKey('K:C ^c _c')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [C-]>

        >>> am = abcFormat.ABCKey('K:^c _c')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [C-]>

        >>> am = abcFormat.ABCKey('K:Db')
        >>> am.getKeySignatureObject()
        <music21.key.Key of D- major>
        '''
        from music21 import key

        if self.mode and self.tonic:
            ks: key.KeySignature = key.Key(self.tonic, self.mode)
            if self.alteredPitches:
                # Apply the additional altered pitches on the given altered pitches of the key
                # keyAltPitch = [ p.name[0]: p.name[1:] for p in ks.alteredPitches ]
                newAltPitch = {p.name[0]: p.name[1:] for p in ks.alteredPitches}
                for a in self.alteredPitches:
                    note, acc = a[0], a[1:]
                    if acc == 'n':
                        # a natural removes a previous setted alteration
                        if note in newAltPitch:
                            del newAltPitch[note]
                    else:
                        newAltPitch[note] = acc

                # if any pitch in the new altered pitches was not part of the
                # altered pitches of the key then the key has changed
                # and we create a Keysignature from the new altered pitches
                if any(pitch not in ks.alteredPitches for pitch in newAltPitch):
                    ks = key.KeySignature()
                    ks.alteredPitches = [f"{n}{a}" for n, a in newAltPitch.items()]

        elif self.alteredPitches:
            # Create a Keysignature from accidentals
            ks = key.KeySignature()
            ks.alteredPitches = self.alteredPitches
        else:
            # With nothing is given we get a Keysignature without any altered pitches
            ks = key.KeySignature(0)

        return ks


class ABCInstruction(ABCMetadata):
    r"""
    ABCInstruction token for the ABC field 'I:'

    The token represent an instruction or abc directive

    >>> i = abcFormat.ABCInstruction('I: accidental-propagation pitch')
    >>> i.key
    'accidental-propagation'
    >>> i.instruction
    'pitch'
    """
    TOKEN_REGEX = makeMetaDataRegexpr('I')

    def __init__(self, src: str):
        super().__init__(src)
        parts = self.data.split(' ', 1)
        self.key = parts[0].strip()
        try:
            self.instruction = parts[1].strip()
        except :
            self.instruction = ''


class ABCMeter(ABCMetadata):
    '''
    If there is a time signature representation available,
    get a numerator, denominator and an abbreviation symbol.
    To get a music21 :class:`~music21.meter.TimeSignature` object, use
    the :meth:`~music21.abcFormat.ABCMeter.getTimeSignatureObject` method.

    >>> am = abcFormat.ABCMeter('M: 2/4')
    >>> am.getTimeSignatureObject()
    <music21.meter.TimeSignature 2/4>
    >>> am.ratio
    0.5
    >>> am.defaultQuarterLength
    0.25
    '''
    TOKEN_REGEX = makeMetaDataRegexpr('M')

    def __init__(self, src):
        super().__init__(src)

        self.symbol: Optional[str] = None
        self.numerators : List[int] = []
        self.denominator : Optional[int] = None
        self.ratio : Optional[float] = None

        if not self.data or self.data.lower() == 'none':
            # M: none is valid abc syntax but not a meter, omnit this token at all
            raise ABCTokenException(f'No meter defined for ABCMeter "{self.src}"')
        elif self.data == 'C':
            self.numerators = [4]
            self.denominator = 4
            self.symbol = 'common'
        elif self.data == 'C|':
            self.numerators = [2]
            self.denominator = 2
            self.symbol = 'cut'
        else:
            parts = self.data.split('/', 1)
            num = parts[0].strip()
            self.symbol = 'normal'
            # using get number from string to handle odd cases such as
            # FREI4/4
            if num:
                self.numerators = [int(n) for n in
                                    common.getNumFromStr(num, numbers='0123456789+')[0].split('+')]
            else:
                # If the numerator is empty we assume it is '1'
                self.numerators = [1]

            try:
                self.denominator = int(common.getNumFromStr(parts[1].strip())[0])
            except ValueError:
                # there is just a digit no fraction
                # set the denumerator to 1
                self.denominator = 1

        self.ratio = sum(self.numerators) / self.denominator

    def getTimeSignatureObject(self) -> Optional['music21.meter.TimeSignature']:
        '''
        Return a music21 :class:`~music21.meter.TimeSignature`
        object for this metadata tag, if isMeter is True, otherwise raise exception.

        >>> am = abcFormat.ABCMeter('^\s*M:2/2')
        >>> am.getTimeSignatureObject()
        <music21.meter.TimeSignature 2/2>

        >>> am = abcFormat.ABCMeter('M:C|')
        >>> am.getTimeSignatureObject()
        <music21.meter.TimeSignature 2/2>

        >>> am = abcFormat.ABCMeter('M:1+2/2')
        >>> am.getTimeSignatureObject()
        <music21.meter.TimeSignature 1/2+2/2>

        >>> am = abcFormat.ABCMeter('M:(2+2+2)/6')
        >>> am.getTimeSignatureObject()
        <music21.meter.TimeSignature 2/6+2/6+2/6>
        '''
        s = "+".join(f'{n}/{self.denominator}' for n in self.numerators)
        try:
            ts = meter.TimeSignature(s)
        except music21.exceptions21.Music21Exception:
            raise ABCTokenException(f'Creating timesignature for "{s}" failed. ({self})')
        return ts

    @property
    def defaultQuarterLength(self) -> float:
        r'''
        If there is a quarter length representation available,
        return it as a floating point value
        If taking from meter, find the "fraction" and if < 0.75 use sixteenth notes.
        If >= 0.75 use eighth notes.

        >>> am = abcFormat.ABCMeter('M:C|')
        >>> am.defaultQuarterLength
        0.5

        >>> am = abcFormat.ABCMeter('M:2/4')
        >>> am.defaultQuarterLength
        0.25

        >>> am = abcFormat.ABCMeter('M:3/4')
        >>> am.defaultQuarterLength
        0.5

        >>> am = abcFormat.ABCMeter('M:6/8')
        >>> am.defaultQuarterLength
        0.5
        '''
        # less than 0.75 the default is a sixteenth note
        return 0.25 if self.ratio < 0.75 else 0.5


class ABCLyrics(ABCMetadata):

    TOKEN_REGEX = makeMetaDataRegexpr('w')

    def __init__(self, src: str):
        '''
        >>> abc = ('w: ||A- ve Ma- ri- -|a! Jung- - - frau *|')
        >>> w = abcFormat.tokenize(abc)
        >>> next(w).syllables
        ['|', '|', 'A-', 've', 'Ma-', 'ri-', '-', '|', 'a!', 'Jung-', '-', '-', 'frau', '*', '|']
        '''
        super().__init__(src)
        self.syllables = [s.replace('~',' ') for s in RE_ABC_LYRICS.findall(self.data)]


class ABCDirective(ABCToken):
    """
    The ABC Directive is a factory for an ABCMetadata object
    with the tag for an instruction (I:)
    >>> token = abcFormat.ABCDirective('%% midi program 1 53')
    >>> token
    <music21.abcFormat.ABCInstruction 'I: midi program 1 53'>
    """
    TOKEN_REGEX = '%%.*'

    def __new__(cls, src: str):
        return ABCInstruction(f'I:{src[2:]}')


class ABCVoiceOverlay(ABCToken):
    TOKEN_REGEX = '&'
    def __init__(self, src):
        super().__init__(src)
        self.handler : 'ABCHandlerVoiceOverlay' = ABCHandlerVoiceOverlay()

    def append(self, token: ABCToken):
        self.handler.tokens.append(token)


class ABCSymbol(ABCToken):
    """
        Redefinable symbols '[H-Wh-w~]'
    """
    TOKEN_REGEX = r'[H-Wh-w~](?![:])'

    DEFAULTS = {
        'H': [ABCDecoration('!fermata!')],
        'L': [ABCDecoration('!accent!')],
        'M': [ABCDecoration('!lowermordent!')],  # is standart, in previous version it was tenuto
        'O': [ABCDecoration('!coda!')],
        'P': [ABCDecoration('!uppermordent!')],
        'S': [ABCDecoration('!segno!')],
        'T': [ABCDecoration('!trill!')],
        'k': [ABCDecoration('!straccent!')],  # no standart
        'K': [ABCDecoration('!accent!')],  # no standart
        'u': [ABCDecoration('!upbow!')],
        'v': [ABCDecoration('!downbow!')],
        '~': []  # Irish roll, not implemented
    }

    def lookup(self, user_defined: Dict[str, List[ABCToken]]) -> List[ABCToken]:
        """
        Lookup the ABCToken in an dictonary of user defined sysmbols. If the
        symbol has not found in the diconary try to lookup the symbol in a
        default dictonary defined by the ABC Standart.
        """
        try:
            return user_defined.get(self.src, ABCSymbol.DEFAULTS[self.src])
        except KeyError:
            raise ABCTokenException(f'Symbol "{self.src}" has no definition')


class ABCBar(ABCToken):
    # The charakter '|', '[' and ']' are barline symbols and regexpr symbols
    # Build the ABCBar regex from a list of subexpressions make it simpler
    TOKEN_REGEX = r"|".join([r':\|[12]?', r'[\|][\|\]:12]?',
                             r'[\[][\|12]', r'[:][\|:]?'])

    # given a logical unit, create an object
    # may be a chord, notes, metadata, bars
    def __init__(self, src):
        '''
        Assign the bar-type based on the source string.
        >>> ab = abcFormat.ABCBar('|')
        >>> ab
        <music21.abcFormat.ABCBar '|'>

        >>> ab.barType
        'barline'
        >>> ab.barStyle
        'regular'

        >>> ab = abcFormat.ABCBar('||')
        >>> ab.barType
        'barline'
        >>> ab.barStyle
        'light-light'

        >>> ab = abcFormat.ABCBar('|:')
        >>> ab.barType
        'repeat'
        >>> ab.barStyle
        'heavy-light'
        >>> ab.repeatForm
        'start'
        '''
        super().__init__(src)
        self.barType = None  # repeat or barline
        self.barStyle = None  # regular, heavy-light, etc
        self.repeatForm = None  # end, start, bidrectional, first, second

        for abcStr, barTypeString in ABC_BARS:
            if abcStr == self.src.strip():
                # this gets lists of elements like
                # light-heavy-repeat-end
                barTypeComponents = barTypeString.split('-')
                # this is a list of attributes
                if 'repeat' in barTypeComponents:
                    self.barType = 'repeat'
                elif ('first' in barTypeComponents
                      or 'second' in barTypeComponents):
                    self.barType = 'barline'
                    # environLocal.printDebug(['got repeat 1/2:', self.src])
                else:
                    self.barType = 'barline'

                # case of regular, dotted
                if len(barTypeComponents) == 1:
                    self.barStyle = barTypeComponents[0]

                # case of light-heavy, light-light, etc
                elif len(barTypeComponents) >= 2:
                    # must get out cases of the start-tags for repeat boundaries
                    # not yet handling
                    if 'first' in barTypeComponents:
                        self.barStyle = 'regular'
                        self.repeatForm = 'first'  # not a repeat
                    elif 'second' in barTypeComponents:
                        self.barStyle = 'regular'
                        self.repeatForm = 'second'  # not a repeat
                    else:
                        self.barStyle = barTypeComponents[0] + '-' + barTypeComponents[1]
                # repeat form is either start/end for normal repeats
                # get extra repeat information; start, end, first, second
                if len(barTypeComponents) > 2:
                    self.repeatForm = barTypeComponents[3]

    def isRepeat(self):
        if self.barType == 'repeat':
            return True
        else:
            return False

    def isRegular(self) -> bool:
        '''
        Return True if this is a regular, single, light bar line.

        >>> ab = abcFormat.ABCBar('|')
        >>> ab.isRegular()
        True
        '''
        if self.barType != 'repeat' and self.barStyle == 'regular':
            return True
        else:
            return False

    def isRepeatBracket(self) -> Union[int, bool]:
        '''
        Return a number if this defines a repeat bracket for an alternate ending
        otherwise returns False.

        >>> ab = abcFormat.ABCBar('[2')
        >>> ab.isRepeat()
        False
        >>> ab.isRepeatBracket()
        2
        '''
        if self.repeatForm == 'first':
            return 1  # we need a number
        elif self.repeatForm == 'second':
            return 2
        else:
            return False

    def m21Object(self) -> Optional['music21.bar.Barline']:
        '''
        Return a music21 bar object

        >>> ab = abcFormat.ABCBar('|:')
        >>> barObject = ab.m21Object()
        >>> barObject
         <music21.bar.Repeat direction=start>
        '''
        from music21 import bar
        if self.isRepeat():
            if self.repeatForm in ('end', 'start'):
                m21bar = bar.Repeat(direction=self.repeatForm)
            # bidirectional repeat tokens should already have been replaced
            # by end and start
            else:  # pragma: no cover
                environLocal.printDebug(
                    [f'found an unsupported repeatForm in ABC: {self.repeatForm}']
                )
                m21bar = None
        elif self.barStyle == 'regular':
            m21bar = None  # do not need an object for regular
        elif self.repeatForm in ('first', 'second'):
            # do nothing, as this is handled in translation
            m21bar = None
        else:
            m21bar = bar.Barline(self.barStyle)
        return m21bar

    @staticmethod
    def barlineTokenFilter(token: str) -> List['ABCBar']:
        '''
        Some single barline tokens are better replaced
        with two tokens. This method, given a token,
        returns a list of tokens. If there is no change
        necessary, the provided token will be returned in the list.

        A staticmethod.  Call on the class itself.

        >>> abcFormat.ABCBar.barlineTokenFilter('::')
        [<music21.abcFormat.ABCBar ':|'>, <music21.abcFormat.ABCBar '|:'>]

        >>> abcFormat.ABCBar.barlineTokenFilter('|2')
        [<music21.abcFormat.ABCBar '|'>, <music21.abcFormat.ABCBar '[2'>]

        >>> abcFormat.ABCBar.barlineTokenFilter(':|1')
        [<music21.abcFormat.ABCBar ':|'>, <music21.abcFormat.ABCBar '[1'>]

        If nothing matches, the original token is returned as an ABCBar object:

        >>> abcFormat.ABCBar.barlineTokenFilter('hi')
        [<music21.abcFormat.ABCBar 'hi'>]
        '''
        barTokens: List[ABCBar] = []
        if token == '::':
            # create a start and and an end
            barTokens.append(ABCBar(':|'))
            barTokens.append(ABCBar('|:'))
        elif token == '|1':
            # create a start and and an end
            barTokens.append(ABCBar('|'))
            barTokens.append(ABCBar('[1'))
        elif token == '|2':
            # create a start and and an end
            barTokens.append(ABCBar('|'))
            barTokens.append(ABCBar('[2'))
        elif token == ':|1':
            # create a start and and an end
            barTokens.append(ABCBar(':|'))
            barTokens.append(ABCBar('[1'))
        elif token == ':|2':
            # create a start and and an end
            barTokens.append(ABCBar(':|'))
            barTokens.append(ABCBar('[2'))
        else:  # append unaltered
            assert token
            barTokens.append(ABCBar(token))
        return barTokens


class ABCTuplet(ABCToken):
    '''
    ABCTuplet tokens always precede the notes they describe.

    In ABCHandler.tokenProcess(), rhythms are adjusted.
    '''
    TOKEN_REGEX = r'\([2-9]:[2-9]?:[2-9]|\([2-9]:[2-9]|\([2-9]'

    def __init__(self, src):
        super().__init__(src)

        # self.qlRemain = None  # how many ql are left of this tuplets activity
        # how many notes are affected by this; this assumes equal duration
        self.noteCount = None

        # actual is tuplet represented value; 3 in 3:2
        self.numberNotesActual = None
        # self.durationActual = None

        # normal is underlying duration representation; 2 in 3:2
        self.numberNotesNormal = None
        # self.durationNormal = None

        # store an m21 tuplet object
        self.tupletObj = None

    def updateRatio(self, keySignatureObj=None):
        # noinspection PyShadowingNames
        '''
        Cannot be called until local meter context
        is established.

        >>> at = abcFormat.ABCTuplet('(3')
        >>> at.updateRatio()
        >>> at.numberNotesActual, at.numberNotesNormal
        (3, 2)

        Generally a 5:n tuplet is 5 in the place of 2.

        >>> at = abcFormat.ABCTuplet('(5')
        >>> at.updateRatio()
        >>> at.numberNotesActual, at.numberNotesNormal
        (5, 2)

        Unless it's in a meter.TimeSignature compound (triple) context:

        >>> at = abcFormat.ABCTuplet('(5')
        >>> at.updateRatio(meter.TimeSignature('6/8'))
        >>> at.numberNotesActual, at.numberNotesNormal
        (5, 3)

        Six is 6:2, not 6:4!

        >>> at = abcFormat.ABCTuplet('(6')
        >>> at.updateRatio()
        >>> at.numberNotesActual, at.numberNotesNormal
        (6, 2)

        >>> at = abcFormat.ABCTuplet('(6:4')
        >>> at.updateRatio()
        >>> at.numberNotesActual, at.numberNotesNormal
        (6, 4)

        >>> at = abcFormat.ABCTuplet('(6::6')
        >>> at.updateRatio()
        >>> at.numberNotesActual, at.numberNotesNormal
        (6, 2)

        2 is 2 in 3...

        >>> at = abcFormat.ABCTuplet('(2')
        >>> at.updateRatio()
        >>> at.numberNotesActual, at.numberNotesNormal
        (2, 3)


        Some other types:

        >>> for n in 1, 2, 3, 4, 5, 6, 7, 8, 9:
        ...     at = abcFormat.ABCTuplet(f'({n}')
        ...     at.updateRatio()
        ...     print(at.numberNotesActual, at.numberNotesNormal)
        1 1
        2 3
        3 2
        4 3
        5 2
        6 2
        7 2
        8 3
        9 2

        Tuplets > 9 raise an exception:

        >>> at = abcFormat.ABCTuplet('(10')
        >>> at.updateRatio()
        Traceback (most recent call last):
        music21.abcFormat.ABCTokenException: cannot handle tuplet of form: '(10'
        '''
        if keySignatureObj is None:
            normalSwitch = 2  # 4/4
        elif keySignatureObj.beatDivisionCount == 3:  # if compound
            normalSwitch = 3
        else:
            normalSwitch = 2

        splitTuplet = self.src.strip().split(':')

        tupletNumber = splitTuplet[0]
        normalNotes = None

        if len(splitTuplet) >= 2 and splitTuplet[1] != '':
            normalNotes = int(splitTuplet[1])

        if tupletNumber == '(1':  # not sure if valid, but found
            a, n = 1, 1
        elif tupletNumber == '(2':
            a, n = 2, 3  # actual, normal
        elif tupletNumber == '(3':
            a, n = 3, 2  # actual, normal
        elif tupletNumber == '(4':
            a, n = 4, 3  # actual, normal
        elif tupletNumber == '(5':
            a, n = 5, normalSwitch  # actual, normal
        elif tupletNumber == '(6':
            a, n = 6, 2  # actual, normal
        elif tupletNumber == '(7':
            a, n = 7, normalSwitch  # actual, normal
        elif tupletNumber == '(8':
            a, n = 8, 3  # actual, normal
        elif tupletNumber == '(9':
            a, n = 9, normalSwitch  # actual, normal
        else:
            raise ABCTokenException(f'cannot handle tuplet of form: {tupletNumber!r}')

        if normalNotes is None:
            normalNotes = n

        self.numberNotesActual = a
        self.numberNotesNormal = normalNotes

    def updateNoteCount(self):
        '''
        Update the note count of notes that are
        affected by this tuplet. Can be set by p:q:r style tuplets.
        Also creates a tuplet object.

        >>> at = abcFormat.ABCTuplet('(6')
        >>> at.updateRatio()
        >>> at.updateNoteCount()
        >>> at.noteCount
        6
        >>> at.tupletObj
        <music21.duration.Tuplet 6/2>

        >>> at = abcFormat.ABCTuplet('(6:4:12')
        >>> at.updateRatio()
        >>> at.updateNoteCount()
        >>> at.noteCount
        12
        >>> at.tupletObj
        <music21.duration.Tuplet 6/4>

        >>> at = abcFormat.ABCTuplet('(6::18')
        >>> at.updateRatio()
        >>> at.updateNoteCount()
        >>> at.noteCount
        18
        '''
        if self.numberNotesActual is None:
            raise ABCTokenException('must set numberNotesActual with updateRatio()')

        # nee dto
        from music21 import duration
        self.tupletObj = duration.Tuplet(
            numberNotesActual=self.numberNotesActual,
            numberNotesNormal=self.numberNotesNormal)

        # copy value; this will be dynamically counted down
        splitTuplet = self.src.strip().split(':')
        if len(splitTuplet) >= 3 and splitTuplet[2] != '':
            self.noteCount = int(splitTuplet[2])
        else:
            self.noteCount = self.numberNotesActual

        # self.qlRemain = self._tupletObj.totalTupletLength()


class ABCTie(ABCToken):
    '''
    Handles instances of ties '-' between notes in an ABC score.
    Ties are treated as an attribute of the note before the '-';
    the note after is marked as the end of the tie.
    '''
    TOKEN_REGEX = r'-'

    def __init__(self, src):
        super().__init__(src)
        self.noteObj = None


class ABCSlurStart(ABCSpanner):
    '''
    ABCSlurStart tokens always precede the notes in a slur.
    For nested slurs, each open parenthesis gets its own token.
    '''
    # Match the start of a slur
    TOKEN_REGEX = r'\((?=[^0-9])'

    def __init__(self, src):
        super().__init__(src, spanner.Slur)


class ABCGraceStart(ABCToken):
    '''
    Grace note start
    '''
    TOKEN_REGEX = r'{'


class ABCGraceStop(ABCToken):
    '''
    <token marks the end of grace notes
    '''
    TOKEN_REGEX = r'}'


class ABCBrokenRhythm(ABCToken):
    '''
    Marks that rhythm is broken with '>>>'
    '''
    TOKEN_REGEX = r'[>]+|[<]+'

    def __init__(self, src):
        super().__init__(src)
        self.left, self.right = {'>': (1.5, 0.5), '>>': (1.75, 0.25),
                                 '>>>': (1.875, 0.125), '<': (0.5, 1.5),
                                 '<<': (0.25, 1.75), '<<<': (0.125, 1.875)
                                 }.get(self.src, (1, 1))


class ABCChordSymbol(ABCMark):
    '''
    A chord symbol
    '''
    TOKEN_REGEX = r'"[^\^<>_@"][^"]*"'

    def __init__(self, src):
        ABCToken.__init__(self, src)
        from music21 import harmony
        src = src[1:-1].strip()
        src = re.sub('[()]', '', src)
        cs_name = common.cleanedFlatNotation(self.src)
        try:
            if cs_name in ('NC', 'N.C.', 'No Chord', 'None'):
                self._m21Object= harmony.NoChord()
            else:
                self._m21Object = harmony.ChordSymbol(cs_name)
        except ValueError:
            self._m21Object = None


# --------------------------------------------------------------------
class ABCGeneralNote(ABCToken):
    '''
    A model of an ABCGeneralNote.

    General usage requires multi-pass processing. After being tokenized,
    each ABCNote needs a number of attributes updates. Attributes to
    be updated after tokenizing, and based on the linear sequence of
    tokens: `inBar`, `inBeam` (not used), `inGrace`,
    `activeDefaultQuarterLength`, `brokenRhythmMarker`, 'articulations'
    'expressions' and `activeKeySignature`.
    '''

    def __init__(self, src: str, length: str):
        """
        Base class for ABCRest, ABCNote & ABCCHord

        Arguments:
            src:
                 token string of an abc note
            length:
                 length string of the abc note
        """
        super().__init__(src)

        # Note length modifier from abc note string
        self.lengthModifier: float = ABCGeneralNote._parse_abc_length(length)

        # A note length modifier provided by an BrokenRythmMarker
        self.brokenRyhtmModifier: float = 1.0

        # context attributes
        self.inBar = None
        self.inBeam = None  # @TODO: not implemented yet
        self.inGrace: bool = False

        # Attach the lyric to the first note with which it begins
        # There maybe multible verses
        self.lyrics = []

        # store a tuplet if active
        self.activeTuplet = None

        # store a spanner if active
        self.activeSpanner = []

        # store a tie if active
        self.tie = None

        # provided expressions & articulations from handler
        self.articulations: List[ABCArticulation] = []
        self.expressions: List[ABCExpression] = []

        # provided default duration from handler
        self.defaultQuarterLength: Optional[float] = None

        # provided key signature from handler
        self.keySignature: Optional['music21.key.KeySignature'] = None

    @staticmethod
    def _parse_abc_length(src: str) -> float:
        '''
        Parse a abc length string.

        arguments:
            src:
                abc note/chord length string
                the function expects only numbers and the slash ('/') to be passed to it.
        returns:
                length modifier als float.

        >>> abcFormat.ABCGeneralNote._parse_abc_length('/')
        0.5
        >>> abcFormat.ABCGeneralNote._parse_abc_length('//')
        0.25
        >>> abcFormat.ABCGeneralNote._parse_abc_length('///')
        0.125
        >>> abcFormat.ABCGeneralNote._parse_abc_length('')
        1.0
        >>> abcFormat.ABCGeneralNote._parse_abc_length('/2')
        0.5
        >>> abcFormat.ABCGeneralNote._parse_abc_length('3/')
        1.5
        >>> abcFormat.ABCGeneralNote._parse_abc_length('4')
        4.0
        >>> abcFormat.ABCGeneralNote._parse_abc_length('3/4')
        0.75
        '''
        if not src:
            return 1.0
        if src == '/':
            return 0.5
        elif src == '//':
            return 0.25
        elif src == '///':
            return 0.125
        else:
            try:
                if src.startswith('/'):
                    # common usage: /4 short for 1/4
                    n, d = 1, int(src.lstrip('/'))
                elif src.endswith('/'):
                    # uncommon usage: 3/ short for 3/2
                    n, d = int(src.strip().rstrip('/')), 2
                elif '/' in src:
                    # common usage: 3/4
                    n, d = src.split('/')
                    n, d = int(n.strip()), int(d.strip())
                else:
                    n, d = int(src), 1
                return n / d
            except ValueError:
                # this is usually an error, provide 1.0 as default
                environLocal.printDebug(['incorrectly encoded / unparsable duration:', src])

        return 1.0

    def quarterLength(self, defaultQuarterLength: Optional[float] = None) -> float:
        """
        Returns the length of this note or rest relative to a quarter note.

        Arguments:
            defaultQuarterLength:
                Optionally, a different DefaultQuarterLength can be specified.
                The quarter note length of the object is not replaced.
        Returns:
            The relative length of this

        >>> abcFormat.ABCNote('=c/2').quarterLength(defaultQuarterLength=0.5)
        0.25
        >>> abcFormat.ABCNote('e2').quarterLength(defaultQuarterLength=0.25)
        0.5

        >>> n = abcFormat.ABCNote('e2')
        >>> n.brokenRyhtmModifier = 0.5
        >>> n.quarterLength(defaultQuarterLength=1.0)
        1.0

        >>> n = abcFormat.ABCNote('e2')
        >>> n.brokenRyhtmModifier = 0.5
        >>> n.quarterLength()
        0.5
        """
        if defaultQuarterLength is None:
            if self.defaultQuarterLength is None:
                defaultQuarterLength = 0.5
            else:
                defaultQuarterLength = self.defaultQuarterLength

        return defaultQuarterLength * self.brokenRyhtmModifier * self.lengthModifier

    def m21Object(self, octave_transposition: Optional[int] = 0):
        """
        return:
            music21 object corresponding to the token.
            Needs implementation of the subclasses
        """
        raise NotImplementedError()

    def apply_tie(self, note: 'musci21.note.Note'):
        from music21 import tie
        if self.tie is not None:
            if self.tie in ('start', 'continue'):
                note.tie = tie.Tie(self.tie)
                note.tie.style = 'normal'
            elif self.tie == 'stop':
                note.tie = tie.Tie(self.tie)

    def apply_expressions(self, note: 'music21.note.GeneralNote'):
        """Add collected expressions to a node/chord object.

        Arguments:
          note:
            Append expressions to this music21 note/chord object
        """
        for e in self.expressions:
            try:
                obj = e.m21Object()
                if note:
                    note.expressions.append(obj)
            except:
                environLocal.printDebug(
                    [f'Create music21 axpression object for Token: "{e}" failed.']
                )

    def apply_spanners(self, obj: 'music21.note.GeneralNote'):
        """
        Add collected spanner to a node/chord object.
        Arguments:
          obj:
            Add spanner to this music21 Note/Chord Object
        """
        for spanner in self.activeSpanner:
            spanner.m21Object().addSpannedElements(obj)

    def apply_articulations(self, note: 'music21.note.GeneralNote'):
        """
        Apply collected articulations to a musci21 node/chord

        Arguments:
          note:
            Apply articulations to this music21 note/chord object
        """

        for a in self.articulations:
            try:
                obj = a.m21Object()
                if note:
                    note.articulations.append(obj)
            except:
                environLocal.printDebug(
                    [f'Create music21 articulation object for Token: "{a.__class__.__name__}" failed.']
                )

    def apply_tuplet(self, note: 'music21.note.GeneralNote'):
        """
        Apply active tuplet to a mucic21 node/chord

        Arguments:
          note:
            Apply active tuplet to this music21 note/chord object
        """
        if self.activeTuplet:
            thisTuplet = copy.deepcopy(self.activeTuplet)
            if thisTuplet.durationNormal is None:
                thisTuplet.setDurationType(note.duration.type, note.duration.dots)
            note.duration.appendTuplet(thisTuplet)


class ABCRest(ABCGeneralNote):
    TOKEN_REGEX = r'[zZx][0-9/]*(?!:)'

    def __init__(self, src):
        super().__init__(src, length=src[1:])

    def m21Object(self, octave_transposition: Optional[int] = 0):
        from music21 import note
        rest = note.Rest()
        rest.duration.quarterLength = self.quarterLength()
        self.apply_tuplet(rest)
        self.apply_tie(rest)
        return rest


class ABCNote(ABCGeneralNote):
    '''
    A model of an ABCNote.

    General usage requires multi-pass processing. After being tokenized,
    each ABCNote needs a number of attributes updates. Attributes to
    be updated after tokenizing, and based on the linear sequence of
    tokens: `inBar`, `inBeam` (not used), `inGrace`,
    `activeDefaultQuarterLength`, `brokenRhythmMarker`, and
    `activeKeySignature`.

    The `chordSymbols` list stores one or more chord symbols (ABC calls
    these guitar chords) associated with this note. This attribute is
    updated when parse() is called.
    '''
    TOKEN_REGEX = r'[=_\^]*[a-gA-G][\',0-9/]*'

    def __init__(self, src: str):
        """
        argument:
            src:
                Token string of an abc note
            carriedaccidental:
                m21 formatted accidental carried from a note of the same bar
            defaultQuarterLength:
                default length of an note an note with qan quarter note is 1.0
                (default value is according ABC standart 0.5 = 1/8)
        """
        p, a, o, l = ABCNote._parse_abc_note(src)
        super().__init__(src, length=l)

        self.pitchClass: str = p  # m21 formated pitch name
        self.accidental: str = a  # m21 formated accidental
        self.octave: int = o  # octave number

        # accidental propagated from a previous note in the same measure
        self.carriedAccidental: str = None
        self._obj = None

    @classmethod
    def _parse_abc_note(cls, src: str) -> Tuple[str, str, int, str]:
        """
        Parse the an abc note string

        argument:
            src:
                Token string of an abc note
        return:
            pitchClass:
                musci21 formated pitchClass of the note
            accidental:
                music21 formated accidental of the note
            octave:
                The octave of the note
            length:
                The abc length of the note

        >>> abcFormat.ABCNote._parse_abc_note('C')
        ('C', '', 4, '')

        >>> abcFormat.ABCNote._parse_abc_note('^c')
        ('C', '#', 5, '')

        >>> abcFormat.ABCNote._parse_abc_note('_c,,')
        ('C', '-', 3, '')

        >>> abcFormat.ABCNote._parse_abc_note("__g'/4")
        ('G', '--', 6, '/4')

        >>> abcFormat.ABCNote._parse_abc_note("^_C'6/4")
        ('C', '-', 5, '6/4')
        """
        ABC_TO_M21_ACCIDENTAL_MAP = {'^': '#', '^^': '##', '=': 'n', '_': '-', '__': '--'}
        match = RE_ABC_NOTE.match(src)
        if match:
            accidental = match.group(1)
            if accidental:
                # If no 2 charakter accidental is found, then
                # try to get a one charakter accidental
                accidental = ABC_TO_M21_ACCIDENTAL_MAP.get(
                    accidental[-2:],
                    ABC_TO_M21_ACCIDENTAL_MAP.get(accidental[-1], ''))
            else:
                accidental = ''
            pitch = match.group(2)
            last = match.group(3)
            if last:
                length, o = common.getNumFromStr(last, '0123456789/')
            else:
                length, o = '', None

            octave = 5 if pitch.islower() else 4
            if o:
                octave -= o.count(',')
                octave += o.count("'")
        else:
            raise ABCTokenException(f'Token string "{src}" is not an abc note.')

        return (pitch.upper(), accidental, octave, length)

    #@functools.cached_property
    def getPitchName(self, octave_transposition: int = 0,
                     keySignature: Optional['music21.key.KeySignature'] = None,
                     carriedAccidental: Optional[str] = None) -> Tuple[str, bool]:
        """
        Parse the note & return
        """
        if keySignature is None:
            keySignature = self.keySignature
        if carriedAccidental is None:
            carriedAccidental = self.carriedAccidental

        octave = self.octave + octave_transposition
        cache_key = (self.pitchClass,
                     octave,
                     carriedAccidental,
                     self.accidental,
                     str(keySignature)
                     )

        try:
            return _pitchTranslationCache[cache_key]

        except KeyError:
            if carriedAccidental:
                active_accidental = carriedAccidental
            elif keySignature:
                active_accidental = next((p.accidental.modifier for p in keySignature.alteredPitches
                                          if p.step == self.pitchClass), None)
            else:
                active_accidental = None

            if active_accidental:
                if not self.accidental:
                    # the abc pitch has no accidental but there is an active accidental
                    accidental, display = active_accidental, False
                elif self.accidental == active_accidental:
                    # the abc pitch has the same accidental as in active accidentals
                    accidental, display = self.accidental, False
                else:
                    # the abc pitch has an accidental but it is not the same as in the active accidentals
                    accidental, display = self.accidental, True
            elif self.accidental:
                # the abc pitch has an accidental but not a an active accidental
                accidental, display = self.accidental, True
            else:
                # the abc pitch has no accidental and no active accidental
                accidental, display = '', None

            result = (f'{self.pitchClass}{accidental}{octave}', display)
            _pitchTranslationCache[cache_key] = result
            return result

    def m21Object(self, octave_transposition: int = 0) -> Union['music21.note.Note', 'music21.note.Rest']:
        """
            Get a music21 note or restz object
            QuarterLength, ties, articulations, expressions, grace,
            spanners and tuplets are applied.
            If this note is a rest, only tuplets, quarterlength and
            spanners are applied.

        return:
            music21 note or rest object corresponding to this token.

        >>> abc = abcFormat.ABCNote("^f'")
        >>> abc.keySignature = key.Key('G')
        >>> n = abc.m21Object()
        >>> n.fullName
        'F-sharp in octave 6 Eighth Note'
        >>> n.pitch.accidental.displayStatus
        False

        >>> n = abcFormat.ABCNote('e2').m21Object()
        >>> n.fullName
        'E in octave 5 Quarter Note'

        >>> n = abcFormat.ABCNote('C').m21Object()
        >>> n.fullName
        'C in octave 4 Eighth Note'

        >>> n = abcFormat.ABCNote('B,').m21Object()
        >>> n.fullName
        'B in octave 3 Eighth Note'

        >>> n = abcFormat.ABCNote("^C'").m21Object()
        >>> n.fullName
        'C-sharp in octave 5 Eighth Note'
        >>> n.pitch.accidental.displayStatus
        True

        >>> an = abcFormat.ABCNote('=c4')
        >>> n = an.m21Object()
        >>> n.fullName
        'C-natural in octave 5 Half Note'
        >>> n.pitch.accidental.displayStatus
        True

        >>> an = abcFormat.ABCNote("_c'/")
        >>> n = an.m21Object()
        >>> n.fullName
        'C-flat in octave 6 16th Note'
        >>> n.pitch.accidental.displayStatus
        True
        """
        from music21 import note
        pitchName, accidentalDisplayStatus = self.getPitchName(octave_transposition=octave_transposition)
        try:
            n = note.Note(pitchName)
        except:
            raise ABCTokenException(f'Pitchname {pitchName} is not valid m21 syntax for a Note')

        if n.pitch.accidental is not None:
            n.pitch.accidental.displayStatus = accidentalDisplayStatus

        n.duration.quarterLength = self.quarterLength()
        self.apply_tuplet(n)
        self.apply_spanners(n)
        self.apply_tie(n)
        if self.inGrace:
            n = n.getGrace()

        self.apply_articulations(n)
        self.apply_expressions(n)
        self._obj = n
        n.pitch.spellingIsInferred = False
        return n


class ABCChord(ABCGeneralNote):
    '''
    A representation of an ABC Chord, which contains within its delimiters individual notes.

    A subclass of ABCNote.
    '''

    # Regular expression matching an ABCCHord
    TOKEN_REGEX = r'[\[][^\]:]*[\]][0-9]*[/]*[0-9]*'

    def __init__(self, src: str, abcVersion: Optional[ABCVersion]=None):
        """
        Token of an abc chord.
        Requires the context of the 'parent' handler
        arguments:
            src:
                Token string of an abc note
            parent:
                ABC Handler of the Chord
        """
        intern, length = src.split(']', 1)
        super().__init__(src, length)
        self.innerStr = intern[1:]

        # Carried acidentials is for the inner tokens
        self.carriedAccidentals: Optional[Dict[str, str]] = None

        # tokenize the inner string of the chord.
        # Only articulations, expressions and notes are relevant
        self.subTokens = [t for t in tokenize(self.innerStr, abcVersion=abcVersion) if
                  isinstance(t, (ABCArticulation, ABCExpression, ABCNote))]

        self._first_note: Optional[ABCNote] = next((t for t in self.subTokens
                                                    if isinstance(t, ABCNote)), None)

    @property
    def isEmpty(self) -> bool:
        """
        A chord without a note is empty even if tokens
        of other types are present.
        """
        return self._first_note is None

    def quarterLength(self, defaultQuarterLength: Optional[float] = None):
        """
        Get the length of this chord relative to a quarter note.
        Requires a processed ABCChord or the argument "defaultQuarterLength" has set.
        If the Chord is not processed or the Argument defaultQuarterLength hasn't set,
        the defaultQuarterLength is 0.5

        Arguments:
            defaultQuarterLength:
                Optionally, a different DefaultQuarterLength can be specified.
                The quarter note length of the object is not replaced.
        Returns:
            CHord length relative to quarter note

        >>> c = abcFormat.ABCChord('[]')
        >>> c.quarterLength(defaultQuarterLength=1.0)
        0.0

        >>> c = abcFormat.ABCChord('[ceg]')
        >>> c.quarterLength(defaultQuarterLength=1.0)
        1.0

        >>> c = abcFormat.ABCChord('[dfa]/2')
        >>> c.quarterLength(defaultQuarterLength=1.0)
        0.5

        >>> c = abcFormat.ABCChord('[e2fg]')
        >>> c.quarterLength()
        1.0

        >>> c = abcFormat.ABCChord('[ADF]3/2')
        >>> c.quarterLength()
        0.75

        >>> c = abcFormat.ABCChord('[ADF]//')
        >>> c.quarterLength()
        0.125

        >>> c = abcFormat.ABCChord('[ADF]///')
        >>> c.quarterLength(defaultQuarterLength=2.0)
        0.25
        """
        if self.isEmpty:
            return 0.0

        return self.lengthModifier * self.brokenRyhtmModifier * self._first_note.quarterLength(defaultQuarterLength)

    def m21Object(self, octave_transposition: int=0) -> Optional['music21.chord.Chord']:
        """
            Get a music21 chord object
            QuarterLength, ties, articulations, expressions, grace,
            spanners and tuplets are applied to the chord.
            In addition, expressions and articulations inside the chord
            are individually assigned to the chord notes.

            If this chord is empty it returns None.

        return:
            music21 chord object corresponding to this token.
        """
        if self.isEmpty:
            return None

        notes = [n.m21Object(octave_transposition) for n in self.subTokens if isinstance(n, ABCNote)]

        from music21.chord import Chord
        c = Chord(notes)
        c.duration.quarterLength = self.quarterLength()

        if self.inGrace:
            c = c.getGrace()

        self.apply_articulations(c)
        self.apply_expressions(c)
        self.apply_spanners(c)
        self.apply_tuplet(c)
        self.apply_tie(c)

        return c


TOKEN_SPEC: Dict[str, Tuple[str, Optional[Callable]]] = {
    'COMMENT': ('%(?=[^%]).*$', None),
    'LINE_CONTINUE': (r'\\n', None),
    'ABCDecoration': (r'!.*?!|[\.]', ABCDecoration)
}


def registerToken(token_class: Type[ABCToken], recursive: bool = True):
    """
    Add a token class and all his subclasses (recursive) to the token specifications
    A token class requires the property 'TOKEN_REGEX' for the token specifications
    The subclasses of ABCSpanner require the properties 'TOKEN_REGEX_START' and
    'TOKEN_REGEX_STOP' for the begin and end of the enclosed abc code.
    """

    if not hasattr(token_class, 'TOKEN_REGEX') or token_class.TOKEN_REGEX is None:
        environLocal.printDebug(
            [f'Missing or empty attribute "TOKEN_REGEX" for "{token_class.__name__}".'])
    else:

        TOKEN_SPEC[f'{token_class.__name__}'] = (f"{token_class.TOKEN_REGEX}", token_class)

    # Search for subclasses of the token class
    if recursive:
        for sub_class in token_class.__subclasses__():
            if sub_class != token_class:
                registerToken(token_class=sub_class, recursive=recursive)


# register all subclasses of ABCToken
registerToken(token_class=ABCToken)

# Build a regular expression for the tokenizer from TOKEN_SPEC
TOKEN_RE = re.compile(r'|'.join(f'(?P<{group}>{spec[0]})'
                                for group, spec in TOKEN_SPEC.items()), flags=re.MULTILINE)


def tokenize(src: str, abcVersion: Optional[ABCVersion] = None) -> Iterator[ABCToken]:
    r'''
    Walk the abc string, creating ABC objects along the way.

    This may be called separately from process(), in the case
    that pre/post parse processing is not needed.

    >>> type(abcFormat.tokenize('X: 1'))
    <class 'generator'>

    >>> list(abcFormat.tokenize('X: 1'))
    [<music21.abcFormat.ABCReferenceNumber 'X: 1'>]

    >>> list(abcFormat.tokenize('M:6/8\nL:1/8\nK:G\nB3 A3 | G6 |'))
    [<music21.abcFormat.ABCMeter 'M:6/8'>, <music21.abcFormat.ABCUnitNoteLength 'L:1/8'>,
    <music21.abcFormat.ABCKey 'K:G'>, <music21.abcFormat.ABCNote 'B3'>, <music21.abcFormat.ABCNote 'A3'>,
    <music21.abcFormat.ABCBar '|'>, <music21.abcFormat.ABCNote 'G6'>, <music21.abcFormat.ABCBar '|'>]
    '''

    for m in TOKEN_RE.finditer(src):
        rule = m.lastgroup
        value = m.group()

        if rule == 'COMMENT':
            continue

        if rule == 'ABCDirective':
            # remove the '%%' start of a directive
            value = value[2:]

        # Some barlines are replaced by multiple tokens
        if rule == 'ABCBar':
            yield from ABCBar.barlineTokenFilter(value)
            continue

        if rule == 'ABCChord':
            yield ABCChord(value, abcVersion)
            continue

        # Lookup an ABCToken class for the rule and create the token
        regex, token_class = TOKEN_SPEC[rule]
        if token_class:
            try:
                yield token_class(value)
            except ABCTokenException as e:
                environLocal.printDebug([e])
        else:
            environLocal.printDebug(
                [f'No token class for rule "{rule}" with matching regex "{regex}"'])

def parseABCVersion(src: str) -> Optional[ABCVersion]:
    '''
    Every abc file conforming to the standard should start with the line
    %abc-2.1

    >>> abcFormat.parseABCVersion('%abc-2.3.2')
    (2, 3, 2)

    Catch only abc version as first comment line
    >>> abcFormat.parseABCVersion('%first comment\\n%abc-2.3.2')

    But ignore post comments
    >>> abcFormat.parseABCVersion('X:1 % reference number\\n%abc-2.3.2')
    (2, 3, 2)
    '''
    verMats = RE_ABC_VERSION.match(src)
    if verMats:
        abcMajor = int(verMats.group(3))
        abcMinor = int(verMats.group(4))
        abcPatch = int(verMats.group(5)) if verMats.group(5) else 0
        return (abcMajor, abcMinor, abcPatch)

class ABCHandler():
    '''
       An ABCHandler is able to divide elements of a character stream into objects and handle
       store in a list, and passes global information to components

       Optionally, specify the (major, minor, patch) version of ABC to process--
       e.g., (1.2.0). If not set, default ABC 1.3 parsing is performed.

       If lineBreaksDefinePhrases is True then new lines within music elements
       define new phrases.  This is useful for parsing extra information from
       the Essen Folksong repertory

       New in v6.2 -- lineBreaksDefinePhrases -- does not yet do anything
       '''

    def __init__(self, tokens: Optional[List[ABCToken]] = None, abcVersion: Optional[ABCVersion] = None):
        # tokens are ABC objects import n a linear stream
        self.abcVersion: Optional[ABCVersion] = abcVersion
        self.tokens: List[ABCToken] = [] if tokens is None else tokens
        self.src: str = ''

    def tokenize(self, src: str):
        self.src = src
        if self.abcVersion is None:
            self.abcVersion = parseABCVersion(src=src)

        self.tokens = list(tokenize(src, self.abcVersion))

    def __len__(self):
        return len(self.tokens)

    def __add__(self, other):
        '''
        Return a new handler adding the tokens in both

        Contrived example appending two separate keys.

        Used in polyphonic metadata merge


        >>> abcStr = 'M:6/8\\nL:1/8\\nK:G\\n'
        >>> ah1 = abcFormat.ABCHandler()
        >>> junk = ah1.process(abcStr)
        >>> len(ah1)
        3

        >>> abcStr = 'M:3/4\\nL:1/4\\nK:D\\n'
        >>> ah2 = abcFormat.ABCHandler()
        >>> junk = ah2.process(abcStr)
        >>> len(ah2)
        3

        >>> ah3 = ah1 + ah2
        >>> len(ah3)
        6
        >>> ah3.tokens[0] == ah1.tokens[0]
        True
        >>> ah3.tokens[3] == ah2.tokens[0]
        True

        '''
        ah = self.__class__()  # will get the same class type
        ah.tokens = self.tokens + other.tokens
        return ah

    def splitByVoice(self) -> ABCTune:
        r"""
        Split the tokens of this ABCHandler into al seperat ABCHandlerVoices for each voice.
        Each voice handler got the all the common metadata token of the abc tune header

        Also return an ABCHandler with all the common Metadata.

        The abc header with the common metadata ends with the first 'K:' field or with the
        first non metadata token (bad abc coding).

        ABC directives are treated as metadata instructions.

        We assume there is a voice with the id '1'.  All tokens in the body that
        cannot be assigned to a voice are assigned to the voice '1'. This happens if no
        voices are defined (monophonic tune) or because bad abc coding.

        >>> abcStr = 'M:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process(abcStr)
        >>> abcTune.voices[0]
        <music21.abcFormat.ABCHandlerVoice object at 0x...>
        >>> [t.src for t in abcTune.voices[0].tokens]
        ['M:6/8', 'L:1/8', 'K:G', 'B3', 'A3', '|', 'G6', '|', 'B3', 'A3', '|', 'G6', '||']

        >>> abcStr = ('M:6/8\nL:1/8\nV: * clef=treble\nK:G\nV:1 name="Whistle" ' +
        ...     'snm="wh"\nB3 A3 | G6 | B3 A3 | G6 ||\nV:2 name="violin" ' +
        ...     'snm="v"\nBdB AcA | GAG D3 | BdB AcA | GAG D6 ||\nV:3 name="Bass" ' +
        ...     'snm="b" clef=bass\nD3 D3 | D6 | D3 D3 | D6 ||')
        >>> ah = abcFormat.ABCHandler()
        >>> ah.tokenize(abcStr)
        >>> abcTune = ah.splitByVoice()
        >>> [t.src for t in abcTune.voices[0].tokens]
        ['M:6/8', 'L:1/8', 'V: * clef=treble', 'K:G', 'V:1 name="Whistle" snm="wh"', 'B3', 'A3',
        '|', 'G6', '|', 'B3', 'A3', '|', 'G6', '||']
        >>> [t.src for t in abcTune.voices[1].tokens]
        ['M:6/8', 'L:1/8', 'V: * clef=treble', 'K:G', 'V:2 name="violin" snm="v"', 'B', 'd', 'B', 'A',
        'c', 'A', '|', 'G', 'A', 'G', 'D3', '|', 'B', 'd', 'B', 'A', 'c', 'A', '|', 'G', 'A', 'G', 'D6', '||']
        >>> [t.src for t in abcTune.voices[2].tokens]
        ['M:6/8', 'L:1/8', 'V: * clef=treble', 'K:G', 'V:3 name="Bass" snm="b" clef=bass',
        'D3', 'D3', '|', 'D6', '|', 'D3', 'D3', '|', 'D6', '||']
        """
        activeVoice = []
        lastOverlayToken : ABCVoiceOverlay = None

        voices = {'1': activeVoice }
        tokenIter = iter(self.tokens)
        header = []
        for token in tokenIter:
            if isinstance(token, ABCMetadata):
                header.append(token)
                if isinstance(token, ABCKey):
                    # Stop, regular end of the tune header
                    break
            else:
                # Not a valid token in Header
                # We asume the body starts here
                # put the token back on top of the iterator
                tokenIter = itertools.chain([token], tokenIter)
                break
        else:
            # there are no body tokens, maybe this is an abc include file ?
            return ABCTune(header=ABCHandler(header), voices=[])


        for token in tokenIter:
            if isinstance(token, ABCVoice):
                if token.voiceId is None or token.voiceId == '*':
                    # error in abc code, the voice has no id or
                    # is the 'every voice' id (illegal in body)
                    # skip this token
                    continue

                # change the active voice
                if token.voiceId in voices:
                    activeVoice = voices[token.voiceId]
                else:
                    activeVoice = []
                    voices[token.voiceId] = activeVoice

                lastOverlayToken = None

            elif isinstance(token, ABCBar):
                lastOverlayToken = None

            elif isinstance(token, ABCVoiceOverlay):
                lastOverlayToken = token
                activeVoice.append(token)
                continue

            if lastOverlayToken:
                lastOverlayToken.append(token)
            else:
                activeVoice.append(token)

        voiceHandler = []
        # Create a new Handler for each voice with the header tokens first.
        for voiceId , voiceTokens in voices.items():
            voice_header = [t for t in header if t.tag in "VLKMUI"]
            vh = ABCHandlerVoice(voiceId=voiceId,
                                 tokens=voice_header + voiceTokens,
                                 abcVersion=self.abcVersion)
            if vh.hasNotes():
                voiceHandler.append(vh)

        # Return the header seperat
        return ABCTune(header=ABCHandler(header), voices=voiceHandler)

    def definesReferenceNumbers(self):
        '''
        Return True if this token structure defines more than 1 reference number,
        usually implying multiple pieces encoded in one file.


        >>> abcStr = 'X:5\\nM:6/8\\nL:1/8\\nK:G\\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.definesReferenceNumbers()  # only one returns False
        False


        >>> abcStr = 'X:5\\nM:6/8\\nL:1/8\\nK:G\\nB3 A3 | G6 | B3 A3 | G6 ||\\n'
        >>> abcStr += 'X:6\\nM:6/8\\nL:1/8\\nK:G\\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.definesReferenceNumbers()  # two tokens so returns True
        True
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')

        count = 0
        for t in self.tokens:
            if isinstance(t, ABCReferenceNumber):
                count += 1
                if count == 2:
                    return True

        return False

    def hasNotes(self, noRests: bool = False) -> bool:
        '''
        If tokens are processed, return True if ABCNote or
        ABCChord classes are defined


        >>> abcStr = 'M:6/8\\nL:1/8\\nK:G\\n'
        >>> ah1 = abcFormat.ABCHandler()
        >>> junk = ah1.process(abcStr)
        >>> ah1.hasNotes()
        False

        >>> abcStr = 'M:6/8\\nL:1/8\\nK:G\\nc1D2'
        >>> ah2 = abcFormat.ABCHandler()
        >>> junk = ah2.process(abcStr)
        >>> ah2.hasNotes()
        True
        '''
        #if not self.tokens:
        #    raise ABCHandlerException('must process tokens before calling')
        if noRests:
            return any(isinstance(t, (ABCNote, ABCChord)) for t in self.tokens)
        else:
            return any(isinstance(t, ABCGeneralNote) for t in self.tokens)

    def splitByReferenceNumber(self) -> Dict[int, 'ABCHandler']:
        # noinspection PyShadowingNames
        r'''
        Split tokens by reference numbers.

        Returns a dictionary of ABCHandler instances, where the reference number
        is used to access the music. If no reference numbers are defined,
        the tune is available under the dictionary entry None.

        >>> abcStr = 'X:5\nM:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||\n'
        >>> abcStr += 'X:6\nM:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> len(ah)
        28
        >>> ahDict = ah.splitByReferenceNumber()
        >>> 5 in ahDict
        True
        >>> 6 in ahDict
        True
        >>> 7 in ahDict
        False

        Each entry is its own ABCHandler object.

        >>> ahDict[5]
        <music21.abcFormat.ABCHandler object at 0x10b0cf5f8>
        >>> len(ahDict[5].tokens)
        14

        Header information (except for comments) should be appended to all pieces.

        >>> abcStrWHeader = '%abc-2.1\nO: Irish\n' + abcStr
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStrWHeader)
        >>> len(ah)
        29
        >>> ahDict = ah.splitByReferenceNumber()
        >>> 5 in ahDict
        True
        >>> 6 in ahDict
        True
        >>> 7 in ahDict
        False

        Did we get the origin header in each score?

        >>> ahDict[5].tokens[0]
        <music21.abcFormat.ABCOrigin 'O: Irish'>
        >>> ahDict[6].tokens[0]
        <music21.abcFormat.ABCOrigin 'O: Irish'>
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')

        ahDict = {}

        # tokens in this list are prepended to all tunes:
        prependToAllList = []
        activeTokens = []
        currentABCHandler = None

        for t in self.tokens:
            if isinstance(t, ABCReferenceNumber):
                if currentABCHandler is not None:
                    currentABCHandler.tokens = activeTokens
                    activeTokens = []
                currentABCHandler = ABCHandler(abcVersion=self.abcVersion)
                referenceNumber = int(t.data)
                ahDict[referenceNumber] = currentABCHandler

            if currentABCHandler is None:
                prependToAllList.append(t)
            else:
                activeTokens.append(t)

        if currentABCHandler is not None:
            currentABCHandler.tokens = activeTokens

        if not ahDict:
            ahDict[None] = ABCHandler()

        for thisABCHandler in ahDict.values():
            thisABCHandler.tokens = prependToAllList[:] + thisABCHandler.tokens

        return ahDict

    def definesMeasures(self):
        r'''
        Returns True if this token structure defines Measures in a normal Measure form.
        Otherwise False


        >>> abcStr = ('M:6/8\nL:1/8\nK:G\nV:1 name="Whistle" ' +
        ...     'snm="wh"\nB3 A3 | G6 | B3 A3 | G6 ||\nV:2 name="violin" ' +
        ...     'snm="v"\nBdB AcA | GAG D3 | BdB AcA | GAG D6 ||\nV:3 name="Bass" ' +
        ...     'snm="b" clef=bass\nD3 D3 | D6 | D3 D3 | D6 ||')
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.definesMeasures()
        True

        >>> abcStr = 'M:6/8\nL:1/8\nK:G\nB3 A3 G6 B3 A3 G6'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.definesMeasures()
        False
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')
        count = 0

        for t in self.tokens:
            if isinstance(t, ABCBar) and t.isRegular():
                # must define at least 2 regular barlines
                # this leave out cases where only double bars are given
                count += 1
                # forcing the inclusion of two measures to count
                if count >= 2:
                    return True
        return False

    def splitByMeasure(self) -> List['ABCHandlerBar']:
        r'''
        Divide a token list by Measures, also
        defining start and end bars of each Measure.

        If a component does not have notes, leave
        as an empty bar. This is often done with leading metadata.

        Returns a list of ABCHandlerBar instances.
        The first usually defines only Metadata
        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\nCG | FA | Cb')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>, <music21.abcFormat.ABCKey 'K:C'>,
        <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        [<music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'A'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'b'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> None

        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('X:1\nL:1/2\n| CG |]')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>,
        <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|]'>

        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\n||CG ||||')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>, <music21.abcFormat.ABCKey 'K:C'>,
        <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        <music21.abcFormat.ABCBar '||'> <music21.abcFormat.ABCBar '||'>

        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\n|CG|')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>, <music21.abcFormat.ABCKey 'K:C'>,
        <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>

        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\nC2 | FC | CB\nK:G\n FA')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>, <music21.abcFormat.ABCKey 'K:C'>,
        <music21.abcFormat.ABCNote 'C2'>]
        [<music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'B'>]
        [<music21.abcFormat.ABCKey 'K:G'>, <music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'A'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> None
        None None

        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\nC2 | FC || CB|\nK:G\n|FA')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>, <music21.abcFormat.ABCKey 'K:C'>, <music21.abcFormat.ABCNote 'C2'>]
        [<music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'B'>]
        [<music21.abcFormat.ABCKey 'K:G'>, <music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'A'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '||'>
        <music21.abcFormat.ABCBar '||'> <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> None


        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\nCG | FC | C[K:G] F')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCUnitNoteLength 'L:1/2'>, <music21.abcFormat.ABCKey 'K:C'>,
        <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        [<music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCKey '[K:G]'>, <music21.abcFormat.ABCNote 'F'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> None

        >>> ah = abcFormat.ABCHandler()
        >>> abcTune = ah.process('L:1/2\nK:C\nCG ||FA||')
        >>> mhl = abcTune.voices[0].splitByMeasure()
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '||'>
        <music21.abcFormat.ABCBar '||'> <music21.abcFormat.ABCBar '||'>
        '''

        def split_tokens(tokens):
            # yields ABCHandlerBars or ABCBar tokens
            bartokens = []
            for t in tokens:
                if isinstance(t, ABCBar):
                    # yield zuerst die bartokens (wenn dann welche da sind) und dann das token ABCBar
                    yield ABCHandlerBar(bartokens)
                    yield t
                    bartokens = []
                elif isinstance(t, ABCMetadata) and not t.inlined:
                    # Metadata (if not inlined) token is the end of a bar
                    # das Metadatatoken gehört zur nächsten Bar
                    yield ABCHandlerBar(bartokens)
                    bartokens = [t]
                else:
                    bartokens.append(t)

            if bartokens:
                yield ABCHandlerBar(bartokens)


        tokeniter = iter(self.tokens)

        # At first get all Header tokens
        header = []
        for token in tokeniter:
            if isinstance(token, ABCMetadata) and not token.inlined:
                header.append(token)
                if isinstance(token, ABCKey):
                    # Regular end of an abc header
                    break
            else:
                # Irrelguar end of an ABC Header
                tokeniter = itertools.chain([token], tokeniter)
                break

        barHandler = []
        emptyBarHandler = ABCHandlerBar(header)
        last = None
        for e in split_tokens(tokeniter):
            if isinstance(e, ABCBar):
                if isinstance(last, ABCHandlerBar):
                    last.rightBarToken = e
            else:

                if e.hasNotes():
                    barHandler.append(e)
                    if emptyBarHandler:
                        e.tokens = emptyBarHandler.tokens + e.tokens
                        emptyBarHandler = None

                else:
                    if emptyBarHandler:
                        emptyBarHandler.tokens = emptyBarHandler.tokens + e.tokens
                    else:
                        emptyBarHandler = e
                    continue

                if isinstance(last, ABCBar):
                    e.leftBarToken = last

            last = e

        return barHandler

    def process(self, src: Optional[str]=None) -> ABCTune:
        if src:
            self.tokenize(src)

        # Use for processing an extra Object, reduce class overhead in ABCHandlerBar
        # Do not process tokens)
        abcTune = self.splitByVoice()

        for voice in abcTune.voices:
            voice.process()

        return abcTune


class ABCHandlerVoice(ABCHandler):
    # tokens are ABC objects in a linear stream
    def __init__(self, tokens: List[ABCToken], voiceId: str = '1', abcVersion: Optional[ABCVersion] = None):
        super().__init__(tokens=tokens, abcVersion=abcVersion)
        self.voiceId = voiceId
        self.transposition = 0
        self.octave = None
        self.clef = None
        self.subname = ''
        self.name = ''

        self._measures = None

    def process(self) -> 'ABCHandlerVoice':
        # Use for processing an extra Object, reduce class overhead in ABCHandlerBar
        # Do not process tokens
        tokenProcessor = ABCVoiceProcessor()
        self.tokens = list(tokenProcessor.process(self))
        return self

    @property
    def measures(self):
        if self._measures is None:
            self._measures = self.splitByMeasure()

        return self._measures

class ABCHandlerVoiceOverlay(ABCHandler):
    # tokens are ABC objects in a linear stream
    def __init__(self, abcVersion: Optional[ABCVersion]=None):
        super().__init__(abcVersion=abcVersion)
        self.overlayId = None

    def process(self, tokenProcessor: 'ABCVoiceProcessor') -> 'ABCHandlerVoice':
        # Use for processing an extra Object, reduce class overhead in ABCHandlerBar
        # Do not process tokens
        self.tokens = list(tokenProcessor.process(self))
        return self

class ABCHandlerBar(ABCHandler):
    '''
    A Handler specialized for storing bars. All left
    and right bars are collected and assigned to attributes.
    '''

    # divide elements of a character stream into objects and handle
    # store in a list, and pass global information to components

    def __init__(self, tokens: Optional[List[ABCToken]] = None,
                 left: Optional[ABCBar] = None,
                 rigth: Optional[ABCBar] = None):

        # tokens are ABC objects in a linear stream
        super().__init__(tokens)
        self.leftBarToken: Optional[ABCBar] = left
        self.rightBarToken: Optional[ABCBar] = rigth

    def addOverlay(self, tokens: List[ABCToken], overlayId: str):
        ol = ABCVoiceOverlay(src='&')
        ol.handler.tokens = tokens
        ol.handler.overlayId = overlayId
        self.tokens.append(ol)

    def __add__(self, other):
        ah = self.__class__()  # will get the same class type
        ah.tokens = self.tokens + other.tokens
        # get defined tokens
        for barAttr in ('leftBarToken', 'rightBarToken'):
            bOld = getattr(self, barAttr)
            bNew = getattr(other, barAttr)
            if bNew is None and bOld is None:
                pass  # nothing to do
            elif bNew is not None and bOld is None:  # get new
                setattr(ah, barAttr, bNew)
            elif bNew is None and bOld is not None:  # get old
                setattr(ah, barAttr, bOld)
            else:
                # if both ar the same, assign one
                if bOld.src == bNew.src:
                    setattr(ah, barAttr, bNew)
                else:
                    # might resolve this by ignoring standard bars and favoring
                    # repeats or styled bars
                    environLocal.printDebug(['cannot handle two non-None bars yet: got bNew, bOld',
                                             bNew, bOld])
                    # raise ABCHandlerException('cannot handle two non-None bars yet')
                    setattr(ah, barAttr, bNew)

        return ah

_TOKEN_PROCESS_CACHE = {}

class ABCTokenProcessor():
    def __init__(self, abcVersion: Optional[ABCVersion]=None):
        self.abcVersion = abcVersion

    def process(self, handler: ABCHandlerVoice) -> Iterator[ABCToken]:
        '''
        Process all token objects any supply contextual informations.

        Each token class calls a method named "process_{token.__class.__name__}"
        for processing. If no method with this name has found it will search for a
        method of one of his base classes.
        '''
        # if not isinstance(handler, ABCHandlerVoice):
        #    raise ABCHandlerException(f'Cannot process {handler}, a <ABCHandlerVoice is required !>')

        self.handler = handler
        if self.abcVersion is None:
            self.abcVersion = handler.abcVersion

        self.accidentalPropagation = self._accidental_propagation()
        tokenIter = iter(handler.tokens)

        while True:
            # get a token from the token iteratur until the StopIteration exception has raised
            try:
                token = next(tokenIter)
                if isinstance(token, ABCSymbol):
                    # this is a special case, we replace the symbol token
                    # with one or more user defined tokens
                    try:
                        # Lookup the symbol in the userDefinedSymbols dict
                        # Has a default symbol dict as fallback.
                        symbol_tokens = token.lookup(self.userDefinedSymbols)
                        # chain the symbols in front of tokenIter and continue iterating
                        tokenIter = itertools.chain(symbol_tokens, tokenIter)
                        continue
                    except ABCTokenException as e:
                        # Not defined symbol
                        environLocal.printDebug([e])
                        continue

                # Caching the method lookup has no performance benefits.
                # find a process method for the token by class name
                try:
                    # some tiny speed up
                    token_process_method = _TOKEN_PROCESS_CACHE[(self, token.__class__)]
                except KeyError:
                    token_process_method = getattr(self, f'process_{token.__class__.__name__}', None)

                    # find a process method for the token by super class name
                    if token_process_method is None:
                        for token_base_class in token.__class__.__bases__:
                            token_method_name = f'process_{token_base_class.__name__}'
                            if hasattr(self, token_method_name):
                                token_process_method = getattr(self, token_method_name)
                                break
                        else:
                            environLocal.printDebug([f'No processing method for token: "{token}" found.'])

                    _TOKEN_PROCESS_CACHE[(self, token.__class__.__name__)] = token_process_method

                if token_process_method is not None:
                    token_process_method(token)

                if token is not None:
                    yield token

            except StopIteration:
                # replace tokens with the collected tokens
                break

from collections import defaultdict

class ABCHeaderProcessor(ABCTokenProcessor):
    def __init__(self, abcVersion: Optional[ABCVersion]=None):
        super().__init__(abcVersion)
        self._tune: Optional
        self.abcVoices = defaultdict(list)
        self.titles = []
        self.composers = []
        self.referenceNumber = None
        self.origin = None

    def process_ABCVoice(self, token: 'abcFormat.ABCVoice'):
        if token.voiceId == '*':
            # the '*' id address all voices
            for abcVoiceTokens in self.abcVoices.values():
                abcVoiceTokens.append(token)
        elif token.voiceId in self.abcVoices:
            self.abcVoices[token.voiceId].append(token)
        else:
            # for new introduced voices, get the '*' (all voices) tokens
            self.abcVoices[token.voiceId] = self.abcVoices['*'] + [token]

    def process_ABCTitle(self, token: ABCTitle):
        self.titles.append(token.data)

    def process_ABCOrigin(self, token: ABCOrigin):
        self.origin = token.data

    def process_ABCComposer(self, token: ABCComposer):
        self.composers.append(token.data)

    def process_ABCReferenceNumber(self, token: ABCReferenceNumber):
        # Convert referenceNumber to a number string
        self.referenceNumber = token.data

class ABCVoiceProcessor(ABCTokenProcessor):
    '''
    An ABCHandler for tokens of a an abc voice.

    is able to divide elements of a character stream into objects and handle
    store in a list, and passes global information to components

    Optionally, specify the (major, minor, patch) version of ABC to process--
    e.g., (1.2.0). If not set, default ABC 1.3 parsing is performed.

    If lineBreaksDefinePhrases is True then new lines within music elements
    define new phrases.  This is useful for parsing extra information from
    the Essen Folksong repertory

    New in v6.2 -- lineBreaksDefinePhrases -- does not yet do anything
    '''

    def __init__(self, abcVersion: Optional[ABCVersion]=None):
        super().__init__(abcVersion)
        self.abcDirectives: Dict[str, str] = {}
        self.userDefinedSymbols: Dict[str, List[ABCToken]] = {}
        self.activeSpanner = []
        self.lastKeySignature = None
        self.lastDefaultQL: Optional[float] = None
        self.carriedAccidentals: Dict[str, str] = {}
        self.lastTimeSignatureObj = None  # an m21 object
        self.lastTupletToken = None  # a token obj; keeps count of usage
        self.lastTieToken = None
        self.lastGraceToken = None
        self.lastNoteToken = None
        self.lastArticulations = []
        self.lastExpressions = []
        self.lastBrokenRhythm = None
        # On this Note, starts the last known lyric line(s)
        self.lastLyricNote: Optional[ABCGeneralNote] = None
        self.handler: ABCHandlerVoice = None
        self.accidentalPropagation = self._accidental_propagation()

        # Use an extra Overlay Proccesor for voice Overlays
        self.overlayCounter = 0
        self.overlayProcessors: List[ABCVoiceProcessor] = []

    def _accidental_propagation(self) -> str:
        '''
        Determine how accidentals should 'carry through the measure.'

        >>> ah = abcFormat.ABCVoiceProcessor(abcVersion=(1, 3, 0))
        >>> ah.accidentalPropagation
        'not'
        >>> ah = abcFormat.ABCVoiceProcessor(abcVersion=(2, 0, 0))
        >>> ah.accidentalPropagation
        'pitch'
        '''
        minVersion = (2, 0, 0)
        if not self.abcVersion or self.abcVersion < minVersion:
            return 'not'

        if 'PROPAGATE-ACCIDENTALS' in self.abcDirectives:
            return self.abcDirectives['PROPAGATE-ACCIDENTALS']
        return 'pitch'  # Default per abc 2.1 standard

    def process_ABCArticulation(self, token: ABCArticulation):
        """
          Process ABCArticulation tokens
        """
        self.lastArticulations.append(token)
        self.overlayCounter = 0

    def process_ABCBar(self, token: ABCBar):
        self.carriedAccidentals = {}
        self.overlayCounter = 0
        return token

    def process_ABCBrokenRhythm(self, token: ABCBrokenRhythm):
        """
        Process ABCBrokenRhythm tokens
        """
        if self.lastNoteToken:
            self.lastBrokenRhythm = token

    def process_ABCChord(self, token: ABCChord):
        """
        Process ABCChord token, calls process_ABCGeneralNote first
        """
        self.process_ABCGeneralNote(token)
        token.carriedAccidentals = self.carriedAccidentals
        cp = ABCVoiceProcessor(abcVersion=self.abcVersion)

        cp.carriedAccidentals = self.carriedAccidentals
        cp.lastKeySignature = self.lastKeySignature
        cp.lastDefaultQL = self.lastDefaultQL
        #cp.lastOctaveTransposition = self.lastOctaveTransposition

        # @TODO: create a ChordProcessor
        token.subTokens = list(cp.process(ABCHandler(token.subTokens)))
        return token

    def process_ABCExpression(self, token: ABCExpression):
        """
        Process ABCExpression tokens
        """
        self.lastExpressions.append(token)

    def process_ABCGeneralNote(self, token: ABCGeneralNote):
        """
        Process the ABCGeneralNote tokens (common for notes, chords and rests)
        """
        if self.lastDefaultQL is None:
            raise ABCHandlerException(
                'no active default note length provided for note processing. '
                + f'{token}'
            )

        token.defaultQuarterLength = self.lastDefaultQL
        token.activeSpanner = self.activeSpanner[:]  # fast copy of a list
        token.keySignature = self.lastKeySignature

        # ends ties one note after they begin
        if self.lastTieToken is not None:
            token.tie = 'stop'
            self.lastTieToken = None

        token.articulations = self.lastArticulations
        self.lastArticulations = []

        token.expressions = self.lastExpressions
        self.lastExpressions = []

        # Grace notes are not included in the tuplet note count
        if self.lastGraceToken is not None:
            token.inGrace = True
        elif self.lastTupletToken is None:
            pass
        elif self.lastTupletToken.noteCount == 0:
            self.lastTupletToken = None
        else:
            self.lastTupletToken.noteCount -= 1
            # add a reference to the note
            token.activeTuplet = self.lastTupletToken.tupletObj

        if self.lastBrokenRhythm:
            self.lastNoteToken.brokenRyhtmModifier = self.lastBrokenRhythm.left
            token.brokenRyhtmModifier = self.lastBrokenRhythm.right
            self.lastBrokenRhythm = None

        if self.lastLyricNote is None or self.lastLyricNote.lyrics:
            # If lastLyricNote has lyrics, we start a new lyric line with this note
            self.lastLyricNote = token

        self.lastNoteToken = token
        return token

    def process_ABCGraceStart(self, token: ABCGraceStart):
        """
        Process the ABCGraceStart token
        """
        self.lastGraceToken = token

    def process_ABCGraceStop(self, token: ABCGraceStart):
        """
        Process ABCGraceStop tokens
        """
        self.lastGraceToken = None

    def process_ABCLyrics(self, token: ABCLyrics):
        if self.lastLyricNote is None:
            environLocal.printDebug(['Found lyrics but no notes to align'])
        else:
            self.lastLyricNote.lyrics.append(token)

    def process_ABCMeter(self, token: ABCMeter):
        self.lastTimeSignatureObj = token.getTimeSignatureObject()
        if self.lastDefaultQL is None:
            self.lastDefaultQL = token.defaultQuarterLength
        return token

    def process_ABCKey(self, token: ABCKey):
        self.lastKeySignature = token.getKeySignatureObject()
        self.lastOctaveTransposition = token.octave
        return token

    def process_ABCUnitNoteLength(self, token: ABCUnitNoteLength):
        self.lastDefaultQL = token.defaultQuarterLength
        return token

    def process_ABCVoice(self, token: ABCVoice):
        self.lastLyricNote = None
        # collect relevant abcVoice tokens
        #if token.voiceId== self.handler.voiceId or token.voiceId == '*':
        return token

    def process_ABCInstruction(self, token: ABCInstruction):
        if token.key:
            self.abcDirectives[token.key] = token.data
            if token.key == 'propagate-accidentals':
                self.accidental_propagation = self.accidentalPropagation()

        return token

    def process_ABCNote(self, token: ABCNote):
        self.process_ABCGeneralNote(token)
        if token.accidental:
            # Remember the active accidentals in the measure
            if self.accidentalPropagation == 'octave':
                self.carriedAccidentals[(token.pitchClass, token.octave)] = token.accidental
            elif self.accidentalPropagation == 'pitch':
                self.carriedAccidentals[token.pitchClass] = token.accidental
        else:
            if self.accidentalPropagation == 'pitch' and token.pitchClass in self.carriedAccidentals:
                token.carriedAccidental = self.carriedAccidentals[token.pitchClass]
            elif self.accidentalPropagation == 'octave' and (
                token.pitchClass, token.octave) in self.carriedAccidentals:
                token.carriedAccidental = self.carriedAccidentals[(token.pitchClass, token.octave)]
        return token

    def process_ABCParenStop(self, token: ABCParenStop):
        # @TODO: we don't need this, base class process method is called anyway
        if self.activeSpanner:
            self.activeSpanner.pop()
        return token

    def process_ABCSpanner(self, token: ABCSpanner):
        """
        Proecss ABCSpanner tokens
        """
        self.activeSpanner.append(token)
        return token

    def process_ABCTie(self, token: ABCTie):
        """
        Prcoess ABCTie tokens
        """
        if self.lastNoteToken and self.lastNoteToken.tie == 'stop':
            self.lastNoteToken.tie = 'continue'
        elif self.lastNoteToken:
            self.lastNoteToken.tie = 'start'
        self.lastTieToken = token
        return token

    def process_ABCTuplet(self, token: ABCTuplet):
        """
        Process ABCTuplet tokens
        """
        token.updateRatio(self.lastTimeSignatureObj)
        # set number of notes that will be altered
        # might need to do this with ql values, or look ahead to nxt
        # token
        token.updateNoteCount()
        self.lastTupletToken = token
        return token

    def process_ABCUserDefinition(self, token: ABCUserDefinition):
        try:
            definition = token.tokenize()
            if definition is None and token.symbol in self.userDefinedSymbols:
                # return value of None indicates the symbol definition should deleted
                del self.userDefinedSymbols[token.symbol]
            else:
                self.userDefinedSymbols[token.symbol] = list(definition)
        except ABCTokenException as e:
            environLocal.printDebug(['Creating token form UserDefined failed.', e])
        return token

    def process_ABCVoiceOverlay(self, token: ABCVoiceOverlay):
        # Count the overly tokens in a bar
        self.overlayCounter += 1
        if self.overlayCounter > len(self.overlayProcessors):
            overlayProcessor = ABCVoiceProcessor(abcVersion=self.abcVersion)
            overlayProcessor.abcDirectives = self.abcDirectives
            overlayProcessor.userDefinedSymbols = self.userDefinedSymbols
            self.overlayProcessors.append(overlayProcessor)
        else:
            overlayProcessor = self.overlayProcessors[self.overlayCounter]

        # update relevant context to the overlay TokenProcessor
        overlayProcessor.lastDefaultQL = self.lastDefaultQL
        overlayProcessor.lastKeySignature = self.lastKeySignature
        overlayProcessor.lastTimeSignatureObj = self.lastTimeSignatureObj
        overlayProcessor.carriedAccidentals = {}
        token.handler.overlayId = f'{self.handler.voiceId}_{self.overlayCounter}'
        token.handler.process(overlayProcessor)

        return token


# ------------------------------------------------------------------------------

class ABCFile(prebase.ProtoM21Object):
    '''
    ABC File or String access

    The abcVersion attribution optionally specifies the (major, minor, patch)
    version of ABC to process-- e.g., (1.2.0).
    If not set, default ABC 1.3 parsing is performed.
    '''

    def __init__(self, abcVersion=None):
        self.abcVersion = abcVersion
        self.file = None
        self.filename = None

    def open(self, filename):
        '''
        Open a file for reading
        '''
        # try:
        self.file = io.open(filename, encoding='utf-8')
        # except
        # self.file = io.open(filename, encoding='latin-1')
        self.filename = filename

    def openFileLike(self, fileLike):
        '''
        Assign a file-like object, such as those provided by
        StringIO, as an open file object.

        >>> from io import StringIO
        >>> fileLikeOpen = StringIO()
        '''
        self.file = fileLike  # already 'open'

    def _reprInternal(self):
        return ''

    def close(self):
        self.file.close()

    def read(self, number=None):
        '''
        Read a file. Note that this calls readstr,
        which processes all tokens.

        If `number` is given, a work number will be extracted if possible.
        '''
        return self.readstr(self.file.read(), number)

    @staticmethod
    def extractReferenceNumber(strSrc: str, number: int) -> str:
        '''
        Extract the string data relating to a single reference number
        from a file that defines multiple songs or pieces.

        This method permits loading a single work from a collection/opus
        without parsing the entire file.

        Here is sample data that is not correct ABC but demonstrates the basic concept:

        >>> fileData = """
        ...   X:1
        ...   Hello
        ...   X:2
        ...   Aloha
        ...   X:3
        ...   Goodbye
        ...   """

        >>> file2 = abcFormat.ABCFile.extractReferenceNumber(fileData, 2)
        >>> print(file2)
        X:2
        Aloha

        If the number does not exist, raises an ABCFileException:

        >>> abcFormat.ABCFile.extractReferenceNumber(fileData, 99)
        Traceback (most recent call last):
        music21.abcFormat.ABCFileException: cannot find requested
            reference number in source file: 99


        If the same number is defined twice in one file (should not be) only
        the first data is returned.

        Changed in v6.2: now a static method.
        '''
        collect = []
        gather = False
        for line in strSrc.split('\n'):
            # must be a single line definition
            # rstrip because of '\r\n' carriage returns
            if line.strip().startswith('X:') and line.replace(' ', '').rstrip() == f'X:{number}':
                gather = True
            elif line.strip().startswith('X:') and not gather:
                # some numbers are like X:0490 but we may request them as 490...
                try:
                    forcedNum = int(line.replace(' ', '').rstrip().replace('X:', ''))
                    if forcedNum == int(number):
                        gather = True
                except TypeError:
                    pass
            # if already gathering and find another ref number definition
            # stop gathering
            elif gather and line.strip().startswith('X:'):
                break

            if gather:
                collect.append(line)

        if not collect:
            raise ABCFileException(
                f'cannot find requested reference number in source file: {number}')

        referenceNumbers = '\n'.join(collect)
        return referenceNumbers

    def readstr(self, strSrc: str, number: Optional[int] = None) -> ABCHandler:
        '''
        Read a string and process all Tokens.
        Returns a ABCHandler instance.
        '''
        if number is not None:
            # will raise exception if cannot be found
            strSrc = self.extractReferenceNumber(strSrc, number)

        abcVersion = parseABCVersion(strSrc) if self.abcVersion is None else self.abcVersion
        handler = ABCHandler(abcVersion=abcVersion)
        handler.tokens = list(tokenize(strSrc, abcVersion))
        return handler


# ------------------------------------------------------------------------------
class Test(unittest.TestCase):

    def testTokenization(self):
        from music21.abcFormat import testFiles
        from collections import Counter

        testData = [
            ('fyrareprisarn', {'ABCNote': 152, 'ABCBrokenRhythm': 42,
                               'ABCBar': 35, 'ABCMetadata': 4, 'ABCInstruction': 1,
                               'ABCReferenceNumber': 1, 'ABCTitle': 1, 'ABCOrigin': 1,
                               'ABCMeter': 1, 'ABCUnitNoteLength': 1, 'ABCKey': 1,
                               'ABCGraceStart': 1, 'ABCGraceStop': 1}),
            ('mysteryReel', {'ABCNote': 153, 'ABCBar': 30, 'ABCSymbol': 6,
                             'ABCTuplet': 3, 'ABCMetadata': 2, 'ABCReferenceNumber': 1,
                             'ABCTitle': 1, 'ABCMeter': 1, 'ABCKey': 1}),
            ('aleIsDear', {'ABCNote': 143, 'ABCRest': 63, 'ABCChord': 32, 'ABCBar': 31,
                           'ABCBrokenRhythm': 13, 'ABCVoice': 2, 'ABCReferenceNumber': 1,
                           'ABCTitle': 1, 'ABCMeter': 1, 'ABCUnitNoteLength': 1,
                           'ABCMetadata': 1, 'ABCTempo': 1, 'ABCKey': 1}),
            ('testPrimitive', {'ABCNote': 79, 'ABCBar': 16, 'ABCArticulation': 4, 'ABCSymbol': 3,
                               'ABCChordSymbol': 2, 'ABCChord': 2, 'ABCMeter': 1, 'ABCExpression': 1,
                               'ABCKey': 1, 'ABCTuplet': 1, 'ABCVoiceOverlay': 1, 'ABCDynamic': 1,
                               'ABCTitle': 1, 'ABCLyrics': 1, 'ABCReferenceNumber':1, 'ABCVoice': 1,
                               'ABCInstruction': 2, 'ABCUserDefinition':1, 'ABCUnitNoteLength':1,
                               'ABCSlurStart': 1, 'ABCParenStop': 1, 'ABCTempo': 1, 'ABCTie': 1,
                               'ABCGraceStart':1, 'ABCGraceStop':1, 'ABCBrokenRhythm': 4,
                               'ABCComposer': 1}),
            ('williamAndNancy', {'ABCNote': 93, 'ABCChordSymbol': 46, 'ABCBar': 25, 'ABCMetadata': 5,
                                 'ABCReferenceNumber': 1, 'ABCTitle': 1, 'ABCMeter': 1, 'ABCKey': 1}),
            ('morrisonsJig', {'ABCNote': 137, 'ABCBar': 33, 'ABCMetadata': 2, 'ABCReferenceNumber': 1,
                              'ABCTitle': 1, 'ABCOrigin': 1, 'ABCMeter': 1, 'ABCUnitNoteLength': 1,
                              'ABCKey': 1}),
            ('guineapigTest', {'ABCNote': 51, 'ABCParenStop': 12, 'ABCSymbol': 8, 'ABCSlurStart': 7,
                               'ABCBar': 5, 'ABCTuplet': 4, 'ABCArticulation': 3, 'ABCTie': 3,
                               'ABCGraceStart': 3, 'ABCGraceStop': 3, 'ABCReferenceNumber': 1,
                               'ABCTitle': 1, 'ABCMeter': 1, 'ABCUnitNoteLength': 1, 'ABCKey': 1,
                               'ABCDimStart': 1})
        ]

        for (testfile, sollCounter) in testData:
            abc = getattr(testFiles, testfile)
            tokens = list(tokenize(abc))

            # count the token classes by name
            istCount = Counter(o.__class__.__name__ for o in tokens)

            for abcClass, soll in sollCounter.items():
                self.assertIn(abcClass, istCount,
                        f'No tokens of class "{abcClass}" found in testfile {testfile}')
                self.assertEqual(soll, istCount[abcClass],
                        f'Wrong number of "{abcClass}" tokens found in testfile {testfile}')


    def testRe(self):

        # @TODO: this test needs a new implementation
        # I have removed all here, because of relevanz
        pass

    def testTextLineContinue(self):
        # Test the regular expression & the test post processing
        matchThis = [
            'w: Line \\\nw: continues \\\nw:here',
            'w: Line \\\nw: continues here\n',
            'w: Line\\\nw: continues here',
            'C: Line \n+: continues here\n',
            'C: Line\n+: continues here',
            'C: Line \n+: continues\n+:here\n',

            'C: Line\n+: continues here\n',
            'O: Line \n+: continues here',
            'w: Line\\\nw: continues\n+:here\n',
            'w: Line \\\n+: continues here',
            'w: Line\n+: continues\\\nw:here\n'
        ]
        for caseId, case in enumerate(matchThis):
            t = list(tokenize(case))
            self.assertEqual(t[0].data, 'Line continues here', (caseId, case))

        # Test if the \\ is ignored in the middle of the string
        t = list(tokenize('w: Line \\ continues here'))
        self.assertEqual(t[0].data, 'Line \ continues here')



    def testTokenMetadata(self):
        from music21.abcFormat import testFiles

        # noinspection SpellCheckingInspection
        for (tf, titleEncoded, meterEncoded, keyEncoded) in [
            (testFiles.fyrareprisarn, 'Fyrareprisarn', '3/4', 'F'),
            (testFiles.mysteryReel, 'Mystery Reel', 'C|', 'G'),
            (testFiles.aleIsDear, 'Ale is Dear, The', '4/4', 'D',),
            (testFiles.kitchGirl, 'Kitchen Girl', '4/4', 'D'),
            (testFiles.williamAndNancy, 'William and Nancy', '6/8', 'G'),
        ]:

            tokens = tokenize(tf)
            for t in tokens:
                if isinstance(t, ABCMetadata):
                    if t.tag == 'T':
                        self.assertEqual(t.data, titleEncoded)
                    elif t.tag == 'M':
                        self.assertEqual(t.data, meterEncoded)
                    elif t.tag == 'K':
                        self.assertEqual(t.data, keyEncoded)

    def testTokenProcessor(self):
        from music21.abcFormat import testFiles

        for tf in [
            testFiles.fyrareprisarn,
            testFiles.mysteryReel,
            testFiles.aleIsDear,
            testFiles.testPrimitive,
            testFiles.kitchGirl,
            testFiles.williamAndNancy,
        ]:
            try:
                tokens = list(tokenize(tf))
                abcHandler = ABCHandler(tokens=tokens)
                abcHandler.process()
            except:
                breakpoint()

    def testNoteParse(self):
        from music21 import key
        self.assertEqual(ABCNote('c').getPitchName(keySignature=key.KeySignature(3)), ('C#5', False))
        self.assertEqual(ABCNote('c').getPitchName(), ('C5', None))
        self.assertEqual(ABCNote('^c').getPitchName(), ('C#5', True))

        ks = key.KeySignature(-3)
        self.assertEqual(ABCNote('B').getPitchName(keySignature=ks), ('B-4', False))

        self.assertEqual(ABCNote('B').getPitchName(), ('B4', None))
        self.assertEqual(ABCNote('_B').getPitchName(), ('B-4', True))

    def testSplitByMeasure(self):

        from music21.abcFormat import testFiles

        ah = ABCHandler()
        ah.process(testFiles.hectorTheHero)
        ahm = ah.splitByMeasure()

        for i, l, r in [(1, '|:', '|'),
                        (2, '|', '|'),
                        (-2, '[1', ':|'),
                        (-1, '[2', '|'),
                        ]:
            # print('expecting', i, l, r, ahm[i].tokens)
            # print('have', ahm[i].leftBarToken, ahm[i].rightBarToken)
            # print()
            bar = ahm[i]
            if l is None:
                self.assertEqual(bar.leftBarToken, None, f'wrong left bar token for bar #{i}')
            else:
                self.assertEqual(bar.leftBarToken.src, l, f'wrong left bar token for bar #{i}')

            if r is None:
                self.assertEqual(bar.rightBarToken, None, f'wrong right bar token for bar #{i}')
            else:
                self.assertEqual(bar.rightBarToken.src, r, f'wrong right bar token for bar #{i}')


    def testSplitByReferenceNumber(self):
        from music21.abcFormat import testFiles

        # a case of leading and trailing meta data
        ah = ABCHandler()
        ah.process(testFiles.theBeggerBoy)
        ahs = ah.splitByReferenceNumber()
        self.assertEqual(len(ahs), 1)
        self.assertEqual(list(ahs.keys()), [5])
        self.assertEqual(len(ahs[5]), 88)  # tokens
        self.assertEqual(ahs[5].tokens[0].src, 'X:5')  # first is retained
        # noinspection SpellCheckingInspection

        ah = ABCHandler()
        ah.process(testFiles.testPrimitivePolyphonic)  # has no reference num
        self.assertEqual(len(ah), 47)  # tokens

        ahs = ah.splitByReferenceNumber()
        self.assertEqual(len(ahs), 1)
        self.assertEqual(list(ahs.keys()), [None])
        self.assertEqual(ahs[None].tokens[0].src, 'M:6/8')  # first is retained
        self.assertEqual(len(ahs[None]), 47)  # tokens

        ah = ABCHandler()
        ah.process(testFiles.valentineJigg)  # has no reference num

        self.assertEqual(len(ah), 247)  # total tokens

        ahs = ah.splitByReferenceNumber()
        self.assertEqual(len(ahs), 3)
        self.assertEqual(sorted(list(ahs.keys())), [166, 167, 168])

        self.assertEqual(ahs[168].tokens[0].src, 'X:168')  # first is retained
        self.assertEqual(len(ahs[168]), 90)  # tokens

        self.assertEqual(ahs[166].tokens[0].src, 'X:166')  # first is retained
        # noinspection SpellCheckingInspection
        self.assertEqual(len(ahs[166]), 68)  # tokens

        self.assertEqual(ahs[167].tokens[0].src, 'X:167')  # first is retained
        self.assertEqual(len(ahs[167]), 89)  # tokens

    def testExtractReferenceNumber(self):
        from music21 import corpus
        fp = corpus.getWork('essenFolksong/test0')

        af = ABCFile()
        af.open(fp)
        ah = af.read(5)  # returns a parsed handler
        af.close()
        self.assertEqual(len(ah), 74)

        af = ABCFile()
        af.open(fp)
        ah = af.read(7)  # returns a parsed handler
        af.close()
        self.assertEqual(len(ah), 83)

        fp = corpus.getWork('essenFolksong/han1')
        af = ABCFile()
        af.open(fp)
        ah = af.read(339)  # returns a parsed handler
        af.close()
        self.assertEqual(len(ah), 101)

    def testVoiceOverlay(self):
        # @TODO:
        pass

    def testClef(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.testClef)
        self.assertEqual(36, len(ah.tokens),
                         f'Number of tokens in "testClef"')

        clef_token = [t for t in ah.tokens if isinstance(t, ABCClef)]
        clef_obj =  [t.clef is not None for t in clef_token]

        self.assertEqual(13, len(clef_token), f'Number of clef token')
        self.assertEqual(13, len(clef_obj), f'Number of known clefs')

    def testTies(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.tieTest)
        self.assertEqual(73, len(ah.tokens),
                         f'Number of tokens in "tieTest"')

        ist_start = sum(t.tie =='start' for t in ah.tokens if
                            isinstance(t, ABCGeneralNote))
        ist_stop = sum(t.tie =='stop' for t in ah.tokens if
                            isinstance(t, ABCGeneralNote))

        self.assertEqual(3, ist_start, f'Number of tied notes (start)')
        self.assertEqual(3, ist_stop, f'Number of tied notes (stop)')

    def testSpanner(self):
        testData = [
            ('crescTest', ABCCrescStart, 1, 75),
            ('dimTest', ABCDimStart, 1, 75),
            ('slurTest', ABCSlurStart, 7, 70),
        ]
        for testfile, abcClass, soll, total in testData:
            from music21.abcFormat import testFiles
            abc = getattr(testFiles, testfile)
            ah = ABCHandler()
            header, voice = ah.process(abc)

            self.assertEqual(total, len(ah.tokens),
                             f'Number of tokens in "{testfile}"')

            ist = sum(isinstance(t, abcClass) for t in voice[0].tokens)

            self.assertEqual(soll, ist,
                             f'Number of spanners of type "{abcClass}" in "{testfile}"')

    def testExpressions(self):
        # @TODO: no testdata !
        from music21.abcFormat import testFiles
        testData = [

        ]

        for testfile, abcClass, m21Class, soll, total in testData:
            abc = getattr(testFiles, testfile)
            ah = ABCHandler()
            ah.process(abc)
            self.assertEqual(total, len(ah.tokens),
                             f'Number of tokens in "{testfile}"')
            ist = 0
            for t in voice[0].tokens:
                if isinstance(t, ABCGeneralNote):
                    if issubclass(abcClass, ABCExpression):
                        ist += any(isinstance(a.m21Object(), m21Class) for a in t.expressions)

            self.assertEqual(soll, ist,
                             f'Number of assigned articulations of type "{m21Class}" in "{testfile}"')


    def testArticulations(self):
        testData = [
            ('bowTest', ABCArticulation, articulations.UpBow, 2, 83),
            ('bowTest', ABCArticulation, articulations.DownBow, 1, 83),
            ('accTest', ABCArticulation, articulations.StrongAccent, 2, 87),
            ('accTest', ABCArticulation, articulations.Accent, 2, 87),
            ('accTest', ABCArticulation, articulations.Tenuto, 2, 87),
            ('accTest', ABCArticulation, articulations.Staccato, 5, 87),
            ('staccTest', ABCArticulation, articulations.Staccato, 5, 80)
        ]

        for testfile, abcClass, m21Class, soll, total in testData:
            from music21.abcFormat import testFiles
            abc = getattr(testFiles, testfile)
            ah = ABCHandler()
            ah.process(abc)
            self.assertEqual(total, len(ah.tokens),
                             f'Number of tokens in "{testfile}"')
            ist = 0
            for t in ah.tokens:
                if isinstance(t, ABCGeneralNote):
                    ist +=any( a.m21Object().__class__ == m21Class for a in t.articulations)

            self.assertEqual(soll, ist,
                    f'Number of assigned articulations of type "{m21Class}" in "{testfile}"')


    def testGrace(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        header, voices = ah.process(testFiles.graceTest)
        self.assertEqual(len(ah), 85)

        graceNotes = sum( t.inGrace for t in voices[0].tokens if isinstance(t, ABCGeneralNote))
        self.assertEqual(graceNotes, 11)



def import_all_abc():
    import music21
    for abc_file in pathlib.Path('../corpus/josquin').glob('**/*.abc'):
        with abc_file.open() as f:
            music21.converter.parse(f.read(), forceSource=True, format='abc')


def benchmark():
    import timeit
    print(timeit.timeit(setup='from __main__ import import_all_abc', stmt='import_all_abc()', number=1))


# ------------------------------------------------------------------------------
# define presented order in documentation
_DOC_ORDER = [ABCFile, ABCHandler, ABCHandlerBar]

if __name__ == '__main__':
    us = environment.UserSettings()
    us['debug'] = True
    import music21
    import cProfile

    #benchmark()
    #cProfile.run('import_all_abc()', sort="tottime")
    avem ="""
    X:1
T:Zocharti Loch
C:Louis Lewandowski (1821-1894)
M:C
Q:1/4=76
%%score (T1 T2) (B1 B2)
V:T1           clef=treble-8  name="Tenore I"   snm="T.I"
V:T2           clef=treble-8  name="Tenore II"  snm="T.II"
V:B1  middle=d clef=bass      name="Basso I"    snm="B.I"  transpose=-24
V:B2  middle=d clef=bass      name="Basso II"   snm="B.II" transpose=-24
K:Gm
%            End of header, start of tune body:
% 1
[V:T1]  (B2c2 d2g2)  | f6e2      | (d2c2 d2)e2 | d4 c2z2 |
[V:T2]  (G2A2 B2e2)  | d6c2      | (B2A2 B2)c2 | B4 A2z2 |
[V:B1]       z8      | z2f2 g2a2 | b2z2 z2 e2  | f4 f2z2 |
[V:B2]       x8      |     x8    |      x8     |    x8   |
% 5
[V:T1]  (B2c2 d2g2)  | f8        | d3c (d2fe)  | H d6    ||
[V:T2]       z8      |     z8    | B3A (B2c2)  | H A6    ||
[V:B1]  (d2f2 b2e'2) | d'8       | g3g  g4     | H^f6    ||
[V:B2]       x8      | z2B2 c2d2 | e3e (d2c2)  | H d6    ||
"""

    #abcStr = 'M:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||'
    #ah = ABCHandler()
    #abcTune = ah.process(abcStr)
    #abcStr = ('M:6/8\nL:1/8\nV: * clef=treble\nK:G\nV:1 name="Whistle" ' +
    #    'snm="wh"\nB3 A3 | G6 | B3 A3 | G6 ||\nV:2 name="violin" ' +
    #    'snm="v"\nBdB AcA | GAG D3 | BdB AcA | GAG D6 ||\nV:3 name="Bass" ' +
    #    'snm="b" clef=bass\nD3 D3 | D6 | D3 D3 | D6 ||')

    #ah = ABCHandler()
    #ah.tokenize(abcStr)
    #abcTune = ah.splitByVoice()

    music21.mainTest(Test)
    # us = environment.UserSettings()
    # us['musicxmlPath'] = '/data/local/MuseScore-3.5.2.312125617-x86_64.AppImage'
    # sys.arg test options will be used in mainTest()
    with pathlib.Path('avemaria.abc').open() as f:
    #with pathlib.Path('Unendliche_Freude.abc').open() as f:
    #for file in pathlib.Path('.').glob('*.abc'):
    #    with file.open() as f:
    #         print(file)
    #         abc = f.read()
    #         s = music21.converter.parse(abc, forceSource=True, format='abc')
    #         #s.show()
    #with pathlib.Path('Unendliche_Freude.abc').open() as f:
    #    avem = f.read()
    #with pathlib.Path('tests/clefs.abc').open() as f:
       avem = f.read()
    b= music21.converter.parse(avem, forceSource=True, format='abc')
    b.show()



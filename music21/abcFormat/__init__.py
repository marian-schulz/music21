# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
# Name:         abc/__init__.py
# Purpose:      parses ABC Notation
#
# Authors:      Christopher Ariza
#               Dylan J. Nagler
#               Michael Scott Cuthbert
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
    'ABCToken',
    'ABCMetadata', 'ABCBar', 'ABCTuplet', 'ABCTie',
    'ABCSlurStart', 'ABCParenStop', 'ABCCrescStart', 'ABCDimStart',
    'ABCStaccato', 'ABCUpbow', 'ABCDownbow', 'ABCAccent', 'ABCStraccent',
    'ABCTenuto', 'ABCGraceStart', 'ABCGraceStop', 'ABCBrokenRhythmMarker',
    'ABCNote', 'ABCChord',
    'ABCHandler', 'ABCHandlerBar',
    'mergeLeadingMetaData',
    'ABCFile',
]

import copy
import io
import pathlib
import re
import unittest
from typing import Union, Optional, List, Tuple, Dict, Iterator
from itertools import tee, islice, chain

from music21 import common
from music21 import environment
from music21 import exceptions21
from music21 import prebase
from music21 import dynamics
from music21 import spanner
from music21 import expressions

from music21.abcFormat import translate

environLocal = environment.Environment('abcFormat')

# for implementation
# see http://abcnotation.com/abc2mtex/abc.txt

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

# Forward declaration of the token specs
TOKEN_SPEC = {}

# store a mapping of ABC representation to pitch values
_pitchTranslationCache = {}

# ------------------------------------------------------------------------------
# note inclusion of w: for lyrics
rePitchName = re.compile('[a-gA-Gz]')
reChordSymbol = re.compile('"[^"]*"')  # non greedy
reChord = re.compile('[.*?]')  # non greedy
RE_ABC_VERSION = re.compile(r'(?:((^[^%].*)?[\n])*%abc-)(\d+)\.(\d+)\.?(\d+)?')
RE_DIRECTIVE = re.compile(r'^%%([a-z\-]+)\s+([^\s]+)(.*)')
RE_PICH_AND_OCTAVE = re.compile(r'[a-gA-Gz][\',]*')
RE_ACCIDENTALS = re.compile('[=_\^]*')


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
    ABCToken objects. The :meth:`~music21.abcFormat.ABCHandler.tokenProcess` method
    then does contextual
    adjustments to all tokens, then calls :meth:`~music21.abcFormat.ABCToken.parse` on all tokens.

    The source ABC string itself is stored in self.src
    '''

    def __init__(self, src=''):
        self.src: str = src  # store source character sequence

    def _reprInternal(self):
        return repr(self.src)

    def parse(self):
        '''
        Dummy method that reads self.src and loads attributes.
        It is called after contextual adjustments.

        It is designed to be subclassed or overridden.
        '''
        pass


class ABCExpressionMarker(ABCToken):
    '''
    Base class of abc score expression marker token
    '''

    def __init__(self, src=''):
        super().__init__(src)

    def m21Object(self):
        pass


class ABCArticulation(ABCToken):
    '''
    Base class of abc note articulation token
    '''

    def __init__(self, src=''):
        super().__init__(src)

    @property
    def m21Name(self):
        return NotImplemented


class ABCExpressions(ABCToken):
    '''
    Base class of abc note expresssion token
    '''

    def __init__(self, src=''):
        super().__init__(src)

    def m21Object(self) -> expressions.Expression:
        return NotImplemented


class ABCMetadata(ABCToken):
    '''
    Defines a token of metadata in ABC.

    >>> md = abcFormat.ABCMetadata('I:linebreak')
    >>> md.src
    'I:linebreak'
    >>> md.tag
    'I'
    >>> md.data
    'linebreak'
    '''

    # given a logical unit, create an object
    # may be a chord, notes, metadata, bars
    def __init__(self, src=''):
        '''
           Called before contextual adjustments and needs
           to have access to data.  Divides a token into
           .tag (a single capital letter or w) and .data representations.

           >>> x = abcFormat.ABCMetadata('T:tagData')
           >>> x.tag
           'T'
           >>> x.data
           'tagData'

           >>> x = abcFormat.ABCMetadata('[T:tagData]')
           >>> x.tag
           'T'
           >>> x.data
           'tagData'

           >>> x = abcFormat.ABCMetadata('C:Tom % first name \\n+:Waits % last name')
           >>> x.tag
           'C'
           >>> x.data
           'Tom Waits'
           '''

        # A field that is too long for one line may be continued by prefixing +: at the
        # start of the following line. For string-type information fields,
        # the continuation is considered to add a space between the two half lines.
        # Also remove comments
        src = " ".join(line.split('%',1)[0].rstrip() for line in src.split('\n+:'))
        super().__init__(src)

        # Detect an inline field & remove the brackets
        if src.startswith('['):
            src = src.rstrip(']').lstrip('[')
            self.inlined = True
        else:
            self.inlined = False

        parts = src.split(':', 1)
        self.tag: str = parts[0].strip()
        self.data: str = parts[1].strip()

    def isLyric(self) -> bool:
        return self.tag == 'w'

    def isUserDefinedSymbol(self) -> bool:
        return self.tag == 'U'

    def isDefaultNoteLength(self) -> bool:
        '''
        Returns True if the tag is "L", False otherwise.
        '''
        return self.tag == 'L'

    def isReferenceNumber(self) -> bool:
        '''
        Returns True if the tag is "X", False otherwise.

        >>> x = abcFormat.ABCMetadata('X:5')
        >>> x.tag
        'X'
        >>> x.isReferenceNumber()
        True
        '''
        return self.tag == 'X'

    def isMeter(self) -> bool:
        '''
        Returns True if the tag is "M" for meter, False otherwise.
        '''
        return self.tag == 'M'

    def isTitle(self) -> bool:
        '''
        Returns True if the tag is "T" for title, False otherwise.
        '''
        return self.tag == 'T'

    def isComposer(self) -> bool:
        '''
        Returns True if the tag is "C" for composer, False otherwise.
        '''
        return self.tag == 'C'

    def isOrigin(self) -> bool:
        '''
        Returns True if the tag is "O" for origin, False otherwise.
        This value is set in the Metadata `localOfComposition` of field.
        '''
        return self.tag == 'O'

    def isVoice(self) -> bool:
        '''
        Returns True if the tag is "V", False otherwise.
        '''
        return self.tag == 'V'

    def isKey(self) -> bool:
        '''
        Returns True if the tag is "K", False otherwise.
        Note that in some cases a Key will encode clef information.

        (example from corpus: josquin/laDeplorationDeLaMorteDeJohannesOckeghem.abc)
        '''
        return self.tag == 'K'

    def isTempo(self) -> bool:
        '''
        Returns True if the tag is "Q" for tempo, False otherwise.
        '''
        return self.tag == 'Q'

    def getLyric(self) -> List[str]:
        '''
        >>> am = abcFormat.ABCMetadata('w:Si- - - - - - - cut ro *  -  -  sa')
        >>> am.getLyric()
        ['Si-', '-', '-', '-', '-', '-', '-', 'cut', 'ro', '*', '-', '-', 'sa']
        >>> am = abcFormat.ABCMetadata('w:Ha-ho')
        >>> am.getLyric()
        ['Ha-', 'ho']
        '''
        RE_LYRIC = re.compile(r'[^*\-_ ]+[-]?|[*\-_]')
        return [s.strip() for s in RE_LYRIC.findall(self.data)]

    def getUserDefinedSymbol(self) -> Optional[Tuple[str, str]]:
        '''
        >>> am = abcFormat.ABCMetadata('U:Z=!trill!')
        >>> am.getUserDefinedSymbol()
        ('Z', 'TRILL')
        '''
        if not (self.isUserDefinedSymbol()):
            raise ABCTokenException(
                'no user defined symbol is associated with this metadata.')

        symbol, definition = self.data.split('=', 1)
        try:
            m = TOKEN_RE.search(definition)
            return symbol, m.lastgroup
        except AttributeError:
            return None

    def getTimeSignatureParameters(self) -> Optional[Tuple[List[int], int, str]]:
        """
        If there is a time signature representation available,
        get a numerator, denominator and an abbreviation symbol.
        To get a music21 :class:`~music21.meter.TimeSignature` object, use
        the :meth:`~music21.abcFormat.ABCMetadata.getTimeSignatureObject` method.
        return Tuple[List[<numerator: int>], <denominator: int>, <symbol: str>]

        >>> am = abcFormat.ABCMetadata('M:2/2')
        >>> am.isMeter()
        True
        >>> am.getTimeSignatureParameters()
        ([2], 2, 'normal')

        >>> am = abcFormat.ABCMetadata('M:C|')
        >>> am.getTimeSignatureParameters()
        ([2], 2, 'cut')

        >>> am = abcFormat.ABCMetadata('M:C')
        >>> am.getTimeSignatureParameters()
        ([4], 4, 'common')

        >>> am = abcFormat.ABCMetadata('M: none')
        >>> am.getTimeSignatureParameters() is None
        True

        >>> am = abcFormat.ABCMetadata('M: FREI4/4')
        >>> am.getTimeSignatureParameters()
        ([4], 4, 'normal')

        >>> am = abcFormat.ABCMetadata('M: 2+2+2/4')
        >>> am.getTimeSignatureParameters()
        ([2, 2, 2], 4, 'normal')

        >>> am = abcFormat.ABCMetadata('M: (3+2)/4')
        >>> am.getTimeSignatureParameters()
        ([3, 2], 4, 'normal')

        >>> am = abcFormat.ABCMetadata('M: 3+2')
        >>> am.getTimeSignatureParameters()
        ([3, 2], 1, 'normal')

        >>> am = abcFormat.ABCMetadata('M: /2')
        >>> am.getTimeSignatureParameters()
        ([1], 2, 'normal')

        >>> am = abcFormat.ABCMetadata('M: 1+2++3/4')
        >>> am.getTimeSignatureParameters()
        ([1, 2, 3], 4, 'normal')
        """

        if not self.isMeter():
            raise ABCTokenException('no time signature associated with this metadata')

        if self.data.lower() == 'none':
            return None
        elif self.data == 'C':
            return [4], 4, 'common'
        elif self.data == 'C|':
            return [2], 2, 'cut'
        else:
            try:
                num, denom = self.data.split('/', 1)
            except ValueError:
                # there is just a digit no fraction
                # use this digit as numerator and set the denumerator to 1
                num = self.data.strip()
                denom = "1"

            # using get number from string to handle odd cases such as
            # FREI4/4
            if num:
                num = common.getNumFromStr(num.strip(), numbers='0123456789+')[0].split('+')
                num = [ int(n) for n in num if n.isdigit() ]
            else:
                # If the numerator is empty we assume it is '1'
                num = [1]

            # set the denominator to 1 if it is empty
            denom = int(common.getNumFromStr(denom.strip())[0]) if denom else 1
            return num, denom, 'normal'

    def getTimeSignatureObject(self) -> Optional['music21.meter.TimeSignature']:
        '''
        Return a music21 :class:`~music21.meter.TimeSignature`
        object for this metadata tag, if isMeter is True, otherwise raise exception.

        >>> am = abcFormat.ABCMetadata('M:2/2')
        >>> ts = am.getTimeSignatureObject()
        >>> ts
        <music21.meter.TimeSignature 2/2>

        >>> am = abcFormat.ABCMetadata('M:C|')
        >>> ts = am.getTimeSignatureObject()
        >>> ts
        <music21.meter.TimeSignature 2/2>

        >>> am = abcFormat.ABCMetadata('M:1+2/2')
        >>> ts = am.getTimeSignatureObject()
        >>> ts
        <music21.meter.TimeSignature 1/2+2/2>

        >>> am = abcFormat.ABCMetadata('M:(2+2+2)/6')
        >>> ts = am.getTimeSignatureObject()
        >>> ts
        <music21.meter.TimeSignature 2/6+2/6+2/6>

        >>> am = abcFormat.ABCMetadata('Q:40')
        >>> am.getTimeSignatureObject()
        Traceback (most recent call last):
        music21.abcFormat.ABCTokenException: no time signature associated with
            this non-metrical metadata.
        '''

        if not self.isMeter():
            raise ABCTokenException(
                'no time signature associated with this non-metrical metadata.')
        from music21 import meter
        parameters = self.getTimeSignatureParameters()

        if parameters is None:
            return None
        else:
            numerator, denominator, symbol = parameters
            ts = meter.TimeSignature("+".join(f'{n}/{denominator}' for n in numerator))
            ts.symbol = symbol
            return ts

    def getKeySignatureParameters(self) -> Tuple[str, str, List[str]]:
        # noinspection SpellCheckingInspection
        '''
        Extract key signature parameters, include indications for mode,
        tonic and alterted pitches of the key signature.
        All values were translated into m21 compatible notation.
        return Tuple[<tonic: str>, <mode: str>, List[<altertedPitch: str>]]

        >>> from music21 import abcFormat

        >>> am = abcFormat.ABCMetadata('K:E exp ^c _a')
        >>> am.getKeySignatureParameters()
        ('E', None, ['C#', 'A-'])

        >>> am = abcFormat.ABCMetadata('K:^c _c')
        >>> am.getKeySignatureParameters()
        (None, None, ['C-'])

        >>> am = abcFormat.ABCMetadata('K:_c =c clef=alto')
        >>> am.getKeySignatureParameters()
        (None, None, ['Cn'])

        >>> am = abcFormat.ABCMetadata('K:')
        >>> am.getKeySignatureParameters()
        (None, None, [])

        >>> am = abcFormat.ABCMetadata('K: exp ^c ^f clef=alto')
        >>> am.getKeySignatureParameters()
        (None, None, ['C#', 'F#'])

        >>> am = abcFormat.ABCMetadata('K:Eb Lydian')
        >>> am.getKeySignatureParameters()
        ('E-', 'lydian', [])

        >>> am = abcFormat.ABCMetadata('K:C ^d')
        >>> am.getKeySignatureParameters()
        ('C', 'major', ['D#'])

        >>> am = abcFormat.ABCMetadata('K:APhry clef=alto')
        >>> am.getKeySignatureParameters()
        ('A', 'phrygian', [])

        >>> am = abcFormat.ABCMetadata('K:G Mixolydian')
        >>> am.getKeySignatureParameters()
        ('G', 'mixolydian', [])

        >>> am = abcFormat.ABCMetadata('K: Edor')
        >>> am.getKeySignatureParameters()
        ('E', 'dorian', [])

        >>> am = abcFormat.ABCMetadata('K: F')
        >>> am.getKeySignatureParameters()
        ('F', 'major', [])

        >>> am = abcFormat.ABCMetadata('K:G')
        >>> am.getKeySignatureParameters()
        ('G', 'major', [])

        >>> am = abcFormat.ABCMetadata('K:Gm')
        >>> am.getKeySignatureParameters()
        ('G', 'minor', [])

        >>> am = abcFormat.ABCMetadata('K:Hp')
        >>> am.getKeySignatureParameters()
        ('D', None, ['F#', 'C#'])

        >>> am = abcFormat.ABCMetadata('K:HP')
        >>> am.getKeySignatureParameters()
        ('C', None, [])

        >>> am = abcFormat.ABCMetadata('K:G ionian')
        >>> am.getKeySignatureParameters()
        ('G', 'ionian', [])

        >>> am = abcFormat.ABCMetadata('K:G aeol')
        >>> am.getKeySignatureParameters()
        ('G', 'aeolian', [])

        >>> am = abcFormat.ABCMetadata('K:Cm ^f _d')
        >>> am.getKeySignatureParameters()
        ('C', 'minor', ['F#', 'D-'])

        >>> am = abcFormat.ABCMetadata('K:G major =F')
        >>> am.getKeySignatureParameters()
        ('G', 'major', ['Fn'])
        '''

        if not self.isKey():
            raise ABCTokenException('no key signature associated with this metadata.')

        # The key signature should be specified with a capital letter (A-G) which
        # may be followed by a # or b for sharp or flat respectively.
        # In addition the mode should be specified (when no mode is indicated, major
        # is assumed).
        # The spaces can be left out, capitalisation is ignored for the modes
        # The key signatures may be modified by adding accidentals, according to the
        # format: K:<tonic> <mode> <accidentals>.
        RE_MATCH_MODE = re.compile(r'(?P<tonic>(H[pP])' +
                                   r'|([A-G][#b]?)?)[ ]*(?P<mode>[a-zA-Z]*)([ ]*(?P<accidentals>.*))')

        # It is possible to use the format  to explicitly
        # format: K:<tonic> exp <accidentals>
        RE_MATCH_EXP = re.compile(r'(?P<tonic>(H[pP])' +
                                  r'|([A-G]?[#b]?))[ ]+exp[ ]+(?P<accidentals>.*)')

        # abc uses b for flat and # for sharp in key tonic spec only
        TonicNames = {'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'G#', 'A#', 'F',
                      'Bb', 'Eb', 'D#', 'Ab', 'E#', 'Db', 'C#', 'Gb', 'Cb'}

        # ABC accidentals mapped to m21 accidentals
        accidentalMap = {'=': 'n', '_': '-', '__': '--', '^': '#', '^^': '##'}

        modeMap = {'dor': 'dorian', 'phr': 'phrygian', 'lyd': 'lydian',
                   'mix': 'mixolydian', 'maj': 'major', 'ion': 'ionian',
                   'aeo': 'aeolian', 'loc': 'locrian', 'min': 'minor',
                   'm': 'minor'}

        keyStr = self.data.strip()
        tonic = None
        mode = None
        match = RE_MATCH_EXP.match(keyStr)

        if not match:
            match = RE_MATCH_MODE.match(keyStr)
            if match:
                # Major is the default mode if mode is missing
                # Only the first 3 letters of the mode are evaluated
                m = match.groupdict()['mode'][:3].lower()
                mode = 'major' if not m else modeMap.get(m, 'major')
            else:
                return (tonic, mode, [])

        a = match.groupdict()['accidentals'].strip()
        t = match.groupdict()['tonic']
        accidentals = {}

        if t == 'Hp':
            # Scotish bagpipe tune
            tonic = 'D'
            mode = None
            accidentals = {'F': '#', 'C': '#'}
        elif t == 'HP':
            tonic = 'C'
            mode = None
        elif t in TonicNames:
            # replace abc flat(b) with m21 flat(-)
            t = t.replace('b', '-')
            tonic = t
        else:
            # without tonic no valid mode
            mode = None

        for accStr in a.split():
            # last char is the note symbol
            note, acc = accStr[-1].upper(), accStr[:-1]
            # the leading chars are accidentals =,^,_
            if acc in accidentalMap and note in 'ABCDEFG':
                accidentals[note] = accidentalMap[acc]

        return (tonic, mode, [f"{n}{a}" for n, a in accidentals.items()])

    def getKeySignatureObject(self) -> 'music21.key.KeySignature':
        # noinspection SpellCheckingInspection,PyShadowingNames
        '''
        Return a music21 :class:`~music21.key.KeySignature` or :class:`~music21.key.Key`
        object for this metadata tag.
        >>> am = abcFormat.ABCMetadata('K:G =F')
        >>> am.getKeySignatureObject()
        <music21.key.Key of G major>

        >>> am = abcFormat.ABCMetadata('K:G ^d')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.KeySignature of pitches: [F#, D#]>

        >>> am = abcFormat.ABCMetadata('K:G')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.Key of G major>

        >>> am = abcFormat.ABCMetadata('K:Gmin')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.Key of g minor>

        >>> am = abcFormat.ABCMetadata('K:E exp ^c ^a')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.KeySignature of pitches: [C#, A#]>

        >>> am = abcFormat.ABCMetadata('K:GM')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.Key of g minor>

        >>> am = abcFormat.ABCMetadata('K:Hp')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [F#, C#]>

        >>> am = abcFormat.ABCMetadata('K:HP')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of no sharps or flats>

        >>> am = abcFormat.ABCMetadata('K:C =c')
        >>> am.getKeySignatureObject()
        <music21.key.Key of C major>

        >>> am = abcFormat.ABCMetadata('K:C ^c =c')
        >>> am.getKeySignatureObject()
        <music21.key.Key of C major>

        >>> am = abcFormat.ABCMetadata('K:C ^c _c')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [C-]>

        >>> am = abcFormat.ABCMetadata('K:^c _c')
        >>> am.getKeySignatureObject()
        <music21.key.KeySignature of pitches: [C-]>

        >>> am = abcFormat.ABCMetadata('K:Db')
        >>> am.getKeySignatureObject()
        <music21.key.Key of D- major>
        '''
        if not self.isKey():
            raise ABCTokenException('no key signature associated with this metadata')

        from music21 import key
        tonic, mode, accidentals = self.getKeySignatureParameters()

        if mode and tonic:
            ks: key.KeySignature = key.Key(tonic, mode)
            if accidentals:
                # Apply the additional altered pitches on the given altered pitches of the key
                # keyAltPitch = [ p.name[0]: p.name[1:] for p in ks.alteredPitches ]
                newAltPitch = {p.name[0]: p.name[1:] for p in ks.alteredPitches}
                for a in accidentals:
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

        elif accidentals:
            # Create a Keysignature from accidentals
            ks = key.KeySignature()
            ks.alteredPitches = accidentals
        else:
            # With nothing is given we get a Keysignature without any altered pitches
            ks = key.KeySignature(0)

        return ks

    def getClefObject(self) -> Tuple[Optional['music21.clef.Clef'], Optional[int]]:
        '''
        Extract any clef parameters stored in the key metadata token.
        Assume that a clef definition suggests a transposition.
        Return both the Clef and the transposition.

        [clef=]<clef name>[<line number>][+8 | -8] [middle=<pitch>] [transpose=<semitones>] [octave=<number>] [stafflines=<lines>]
        Returns a two-element tuple of clefObj and transposition in semitones

        >>> am = abcFormat.ABCMetadata('K:Eb Lydian bass')
        >>> am.getClefObject()
        (<music21.clef.BassClef>, -24)

        >>> am = abcFormat.ABCMetadata('K:Eb clef=bass')
        >>> am.getClefObject()
        (<music21.clef.BassClef>, -24)

        >>> am = abcFormat.ABCMetadata('V: clef=alto')
        >>> am.getClefObject()
        (<music21.clef.AltoClef>, 0)

        >>> am = abcFormat.ABCMetadata('V: clef=treble')
        >>> am.getClefObject()
        (<music21.clef.TrebleClef>, 0)

        ABC clef specification
        Treble 	        K:treble
        Bass 	        K:bass
        Baritone 	    K:bass3
        Tenor 	        K:tenor
        Alto 	        K:alto
        Mezzosoprano 	K:alto2
        Soprano 	    K:alto1
        '''
        if not (self.isKey() or self.isVoice()):
            raise ABCTokenException(
                'no key ore voice signature associated with this metadata; needed for getting Clef Object')
        from music21 import clef

        CLEFS = {
            ('-8va', clef.Treble8vbClef, -12),
            ('bass', clef.BassClef, -24),
            ('alto', clef.AltoClef, 0),
            ('treble', clef.TrebleClef, 0),
            ('tenor', clef.TenorClef, 0),
            ('alto1', clef.SopranoClef, 0),
            ('alto2', clef.MezzoSopranoClef, 0),
            ('bass3', clef.CBaritoneClef, 0),
        }
        data = self.data.lower()
        for clefStr, clefClass, transpose in CLEFS:
            if clefStr in data:
                clefObj = clefClass()
                return clefObj, transpose

        return None, None
        # if not defined, returns None, None

    def getMetronomeMarkObject(self) -> Optional['music21.tempo.MetronomeMark']:
        '''
        Extract any tempo parameters stored in a tempo metadata token.

        >>> am = abcFormat.ABCMetadata('Q: "Allegro" 1/4=120')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark Allegro Quarter=120.0>

        >>> am = abcFormat.ABCMetadata('Q: 3/8=50 "Slowly"')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark Slowly Dotted Quarter=50.0>

        >>> am = abcFormat.ABCMetadata('Q:1/2=120')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark animato Half=120.0>

        >>> am = abcFormat.ABCMetadata('Q:1/4 3/8 1/4 3/8=40')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark grave Whole tied to Quarter (5 total QL)=40.0>

        >>> am = abcFormat.ABCMetadata('Q:90')
        >>> am.getMetronomeMarkObject()
        <music21.tempo.MetronomeMark maestoso Quarter=90.0>

        '''
        if not self.isTempo():
            raise ABCTokenException('no tempo associated with this metadata')
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
        return mmObj

    def getDefaultQuarterLength(self) -> Optional[float]:
        r'''
        If there is a quarter length representation available, return it as a floating point value

        >>> am = abcFormat.ABCMetadata('L:1/2')
        >>> am.getDefaultQuarterLength()
        2.0

        >>> am = abcFormat.ABCMetadata('L:1/8')
        >>> am.getDefaultQuarterLength()
        0.5

        >>> x = 'L:1/4\nM:3/4\n\nf'
        >>> sc = converter.parse(x, format='abc')
        >>> sc.flat.notes[0].duration.type
        'quarter'
        '''
        # environLocal.printDebug(['getDefaultQuarterLength', self.data])
        if not self.isDefaultNoteLength():
            raise ABCTokenException(
                f'no quarter length associated with this metadata: {self.data}')

        if self.isDefaultNoteLength() and '/' in self.data:
            # should be in L:1/4 form
            n, d = self.data.split('/')
            n = int(n.strip())
            # the notation L: 1/G is found in some essen files
            # this is extremely uncommon and might be an error
            if d == 'G':
                d = 4  # assume a default
            else:
                d = int(d.strip())
            # 1/4 is 1, 1/8 is 0.5
            return n * 4 / d

        elif self.isMeter():
            # if meter auto-set a default not length
            parameters = self.getTimeSignatureParameters()
            if parameters is None:
                return 0.5  # TODO: assume default, need to configure
            n, d, _ = parameters
            if sum(n) / d < 0.75:
                return 0.25  # less than 0.75 the default is a sixteenth note
            else:
                return 0.5  # otherwise it is an eighth note
        else:
            return None


class ABCBar(ABCToken):
    # given a logical unit, create an object
    # may be a chord, notes, metadata, bars
    def __init__(self, src):
        super().__init__(src)
        self.barType = None  # repeat or barline
        self.barStyle = None  # regular, heavy-light, etc
        self.repeatForm = None  # end, start, bidrectional, first, second

    def parse(self):
        '''
        Assign the bar-type based on the source string.

        >>> ab = abcFormat.ABCBar('|')
        >>> ab.parse()
        >>> ab
        <music21.abcFormat.ABCBar '|'>

        >>> ab.barType
        'barline'
        >>> ab.barStyle
        'regular'

        >>> ab = abcFormat.ABCBar('||')
        >>> ab.parse()
        >>> ab.barType
        'barline'
        >>> ab.barStyle
        'light-light'

        >>> ab = abcFormat.ABCBar('|:')
        >>> ab.parse()
        >>> ab.barType
        'repeat'
        >>> ab.barStyle
        'heavy-light'
        >>> ab.repeatForm
        'start'
        '''
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
        >>> ab.parse()
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
        >>> ab.parse()
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

    def getBarObject(self) -> Optional['music21.bar.Barline']:
        '''
        Return a music21 bar object

        >>> ab = abcFormat.ABCBar('|:')
        >>> ab.parse()
        >>> barObject = ab.getBarObject()
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


class ABCTuplet(ABCToken):
    '''
    ABCTuplet tokens always precede the notes they describe.

    In ABCHandler.tokenProcess(), rhythms are adjusted.
    '''

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
        self.m21Object = None

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
        >>> at.m21Object
        <music21.duration.Tuplet 6/2>

        >>> at = abcFormat.ABCTuplet('(6:4:12')
        >>> at.updateRatio()
        >>> at.updateNoteCount()
        >>> at.noteCount
        12
        >>> at.m21Object
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
        self.m21Object = duration.Tuplet(
            numberNotesActual=self.numberNotesActual,
            numberNotesNormal=self.numberNotesNormal)

        # copy value; this will be dynamically counted down
        splitTuplet = self.src.strip().split(':')
        if len(splitTuplet) >= 3 and splitTuplet[2] != '':
            self.noteCount = int(splitTuplet[2])
        else:
            self.noteCount = self.numberNotesActual

        # self.qlRemain = self._tupletObj.totalTupletLength()


class ABCSpanner(ABCToken):
    def __init__(self, src):
        super().__init__(src)
        self.m21Object = None


class ABCTie(ABCToken):
    '''
    Handles instances of ties '-' between notes in an ABC score.
    Ties are treated as an attribute of the note before the '-';
    the note after is marked as the end of the tie.
    '''

    def __init__(self, src):
        super().__init__(src)
        self.noteObj = None


class ABCSlurStart(ABCSpanner):
    '''
    ABCSlurStart tokens always precede the notes in a slur.
    For nested slurs, each open parenthesis gets its own token.
    '''

    def __init__(self, src):
        super().__init__(src)
        self.m21Object = spanner.Slur()


class ABCParenStop(ABCToken):
    '''
    A general parenthesis stop;
    comes at the end of a tuplet, slur, or dynamic marking.
    '''


class ABCCrescStart(ABCSpanner):
    '''
    ABCCrescStart tokens always precede the notes in a crescendo.
    These tokens coincide with the string "!crescendo(";
    the closing string "!crescendo)" counts as an ABCParenStop.
    '''

    def __init__(self, src):
        super().__init__(src)
        self.m21Object = dynamics.Crescendo()


class ABCDimStart(ABCSpanner):
    '''
    ABCDimStart tokens always precede the notes in a diminuendo.
    They function identically to ABCCrescStart tokens.
    '''

    def __init__(self, src):  # previous typo?: used to be __init
        super().__init__(src)
        self.m21Object = dynamics.Diminuendo()


class ABCStaccato(ABCArticulation):
    '''
    ABCStaccato tokens "." precede a note or chord;
    they are a property of that note/chord.
    '''

    @property
    def m21Name(self) -> str:
        return 'staccato'


class ABCUpbow(ABCArticulation):
    '''
    ABCStaccato tokens "." precede a note or chord;
    they are a property of that note/chord.
    '''

    @property
    def m21Name(self) -> str:
        return 'upbow'


class ABCDownbow(ABCArticulation):
    '''
    ABCStaccato tokens "." precede a note or chord;
    they are a property of that note/chord.
    '''

    @property
    def m21Name(self) -> str:
        return 'downbow'


class ABCAccent(ABCArticulation):
    '''
    ABCAccent tokens "K" precede a note or chord;
    they are a property of that note/chord.
    These appear as ">" in the output.
    '''

    @property
    def m21Name(self) -> str:
        return 'accent'


class ABCStraccent(ABCArticulation):
    '''
    ABCStraccent tokens "k" precede a note or chord;
    they are a property of that note/chord.
    These appear as "^" in the output.
    '''

    @property
    def m21Name(self) -> str:
        return 'strongaccent'


class ABCTenuto(ABCArticulation):
    '''
    ABCTenuto tokens "M" precede a note or chord;
    they are a property of that note/chord.
    '''

    @property
    def m21Name(self) -> str:
        return 'tenuto'


class ABCGraceStart(ABCToken):
    '''
    Grace note start
    '''


class ABCGraceStop(ABCToken):
    '''
    Grace note end
    '''


class ABCTrill(ABCExpressions):
    '''
    Trill
    '''

    def m21Object(self) -> expressions.Trill:
        return expressions.Trill()


class ABCRoll(ABCToken):
    '''
    Irish Roll (Not Supported ?)
    '''
    pass


class ABCFermata(ABCExpressions):
    '''
    Fermata
    '''

    def m21Object(self) -> expressions.Fermata:
        return expressions.Fermata()


class ABCLowerMordent(ABCExpressions):
    '''
    Lower mordent is a single rapid alternation with
    the note below
    '''

    def m21Object(self) -> expressions.Mordent:
        mordent = expressions.Mordent()
        mordent.direction = 'down'
        return mordent


class ABCUpperMordent(ABCExpressions):
    '''
    Upper mordent is a single rapid alternation with
    the note above
    '''

    def m21Object(self) -> expressions.Mordent:
        mordent = expressions.Mordent()
        mordent.direction = 'up'
        return mordent


class ABCCoda(ABCExpressionMarker):
    '''
    Coda
    '''

    def m21Object(self) -> 'music21.repeat.Coda':
        from music21 import repeat
        return repeat.Coda()


class ABCSegno(ABCExpressionMarker):
    '''
    Segno
    '''

    def m21Object(self) -> 'music21.repeat.Segno':
        """
        return:
            music21 object corresponding to the token
        """
        from music21 import repeat
        return repeat.Segno()


class ABCBrokenRhythmMarker(ABCToken):
    '''
    Marks that rhythm is broken with '>>>'
    '''

    def __init__(self, src: str):
        super().__init__(src)
        self.left, self.right = {'>': (1.5, 0.5), '>>': (1.75, 0.25),
                                 '>>>': (1.875, 0.125), '<': (0.5, 1.5),
                                 '<<': (0.25, 1.75), '<<<': (0.125, 1.875)
                                 }.get(self.src, (1, 1))


    def set_notes(self, left: 'ABCGeneralNote', right: 'ABCGeneralNote'):
        '''
        Set note the length modifier of the the BrokenRythm to the left
        and right node
        arguments:
            left:
                Note token to the left of the BrokenRythmMarker
            right:
                Note token to the right of the BrokenRythmMarker
        '''
        left.brokenRyhtmModifier = self.left
        right.brokenRyhtmModifier = self.right


class ABCChordSymbol(ABCToken):
    '''
    Grace note end
    '''

    def __init__(self, src):
        super().__init__(src[1:-1])


class ABCGeneralNote(ABCToken):
    '''
    A model of an ABCGeneralNote.

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

    def __init__(self, src, length: Optional[str]=None,
                 defaultQuarterLength: float=0.5,
                 activeKeySignature: Optional['musci21.key.KeySiganture'] = None):
        """
         argument:
             src:
                 token string of an abc note
            length:
                 length string of the abc note
             defaultQuarterLength:
                 default length of an note an note with qan quarter note is 1.0
                 (default value is according ABC standart 0.5 = 1/8)
             keySignature:
                the active key signature
        """
        super().__init__(src)

        # Note length modifier from abc note string
        self.lengthModifier: float = ABCGeneralNote._parse_length(length)

        # A note length modifier provided by an BrokenRythmMarker
        self.brokenRyhtmModifier: float = 1.0

        # context attributes
        self.inBar = None
        self.inBeam = None
        self.inGrace = None

        # store a tuplet if active
        self.activeTuplet = None

        # store a spanner if active
        self.applicableSpanners = []

        # store a tie if active
        self.tie = None

        # store articulations if active
        # @TODO: Remove this, the Chord doesn't need it but his notes
        self.articulations = []

        # provide default duration from handler; may change during piece
        # @TODO: Remove this, the Chord doesn't need it but his notes
        self.activeDefaultQuarterLength: float = defaultQuarterLength

        # store key signature for pitch processing; this is an m21 object
        # @TODO: Remove this, the Chord doesn't need it but his notes
        self.activeKeySignature = activeKeySignature

        # set to True if a modification of key signature
        # set to False if an altered tone part of a Key
        # @TODO: Remove this, the Chord doesn't need it but his notes
        # self.accidentalDisplayStatus = None

    @classmethod
    def _parse_length(self, src: str) -> float:
        """
        ABCGeneralNote._parse_length('1')
        1.0
        ABCGeneralNote._parse_length('2/3')
        1.5
        ABCGeneralNote._parse_length('/')
        0.5
        ABCGeneralNote._parse_length('//')
        0.25
        ABCGeneralNote._parse_length('/2')
        0.5
        ABCGeneralNote._parse_length('3/')
        1.5
        """
        # numStr, _ = common.getNumFromStr(src, '0123456789/')
        # Base length
        if src is None:
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
                    n, d = 1, int(src.lstrip('/'))
                # uncommon usage: 3/ short for 3/2
                elif src.endswith('/'):
                    n, d = int(src.strip().rstrip('/')), 2
                elif '/' in src:
                    n, d = src.split('/')
                    n, d = int(n.strip()), int(d.strip())
                else:
                    n, d = int(src), 1
                return n / d
            except ValueError:
                # this is usually an error, provide 1.0 as default
                environLocal.printDebug(['incorrectly encoded / unparsable duration:', src])

        return 1.0

    def quarterLength(self, defaultQuarterLength: Optional[float]=None):
        """
        >>> abcFormat.ABCNote('=c/2', defaultQuarterLength=0.5).quarterLength()
        0.25

        >>> abcFormat.ABCNote('e2', defaultQuarterLength=0.5).quarterLength()
        1.0

        >>> abcFormat.ABCNote('e2').quarterLength()
        2.0

        >>> abcFormat.ABCNote('A3/2').quarterLength()
        1.5

        >>> abcFormat.ABCNote('A//').quarterLength()
        0.25

        >>> abcFormat.ABCNote('A///').quarterLength()
        0.125
        """
        # <lengthModifier>: modifier providet by the abc length syntax
        # <brokenRyhtm>: a BrokenRythm modifier
        # <activeDefaultQuarterLength>: default length providet by abc default_note_length (L:)
        if defaultQuarterLength is None:
            defaultQuarterLength = self.activeDefaultQuarterLength
        return self.lengthModifier * self.brokenRyhtmModifier * defaultQuarterLength

    def m21Object(self):
        """
        return:
            music21 object corresponding to the token
        """
        raise NotImplementedError()

    def apply_accents(self, c):
        pass

    def apply_articulations(self, c):
        pass

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

    def __init__(self, src:str, carriedAccidental: Optional[str]=None,
                 defaultQuarterLength: Optional[float]=0.5,
                 activeKeySignature: Optional['musci21.key.KeySignature']=None):
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
        p, a, o, l = ABCNote._parse_note(src)
        super().__init__(src,
                         length=l,
                         defaultQuarterLength=defaultQuarterLength,
                         activeKeySignature=activeKeySignature)

        self.pitch_name: str = p        # m21 formated pitch name
        self.accidental: str = a       # m21 formated accidental
        self.octave: int = o            # octave number

        # accidental propagated from a previous note in the same measure
        self.carriedAccidental: str = carriedAccidental

        # Note is a rest
        self.isRest: boolean = self.pitch_name == 'Z'


    @classmethod
    def _parse_note(cls, src: str) -> Tuple[str, str, int, str]:
        """
        Parse the an abc note string

        argument:
            src:
                Token string of an abc note
        return:
            pitch:
                musci21 formated pitch of the note
            accidental:
                music21 formated accidental of the note
            octave:
                The octave of the note
            lengthStr:
                The abc length of the note

        >>> ABCNote._parse_note('C')
        ('', 'C', 5, '')

        >>> ABCNote._parse_note('^c')
        ('C', '#', 4, '')

        >>> ABCNote._parse_note('_c,,')
        ('C', '-', 6, '')

        >>> ABCNote._parse_note("_g'/4")
        ('G', '-', 6, '/4')

        >>> ABCNote._parse_note("^_g'6/4")
        ('F', '-', 6, '6/3')
        """
        RE_MATCH_ABC_NOTE = re.compile(r'([\^_=])*([A-Ga-gz])([0-9/\',])*').match
        ACCIDENTAL_MAP = {'^': '#', '^^': '##', '=': 'n', '_': '-', '__': '--'}

        match = RE_MATCH_ABC_NOTE(src)
        if match:
            g = match.groups()
            accidental = match.group(1)
            if accidental:
                accidental = ACCIDENTAL_MAP.get(accidental[-2:],
                                                ACCIDENTAL_MAP.get(accidental[:-1]))
            pitch = match.group(2)
            last = match.group(3)
            if last:
                length, o = common.getNumFromStr(last, '0123456789/')
            else:
                length, o = None, None

            octave = 5 if pitch.islower() else 4
            if o:
                octave -= o.count(',')
                octave += o.count("'")
        else:
            raise ABCTokenException(f'Token string "{src}" is not an abc note.')

        return (pitch.upper(), accidental, octave, length)


    def m21Object(self) -> 'music21.note.Note':
        '''
        Given a note or rest string without a chord symbol,
        return a music21 pitch string or None (if a rest),
        and the accidental display status. This value is paired
        with an accidental display status. Pitch alterations, and
        accidental display status, are adjusted if a key is
        declared in the Note.

        >>> an = abcFormat.ABCNote('e2')
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('E5', None)

        >>> an = abcFormat.ABCNote('C')
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('E5', None)

        >>> an = abcFormat.ABCNote('B,,')
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('E5', None)

        >>> an = abcFormat.ABCNote('C,')
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('E5', None)

        >>> an = abcFormat.ABCNote('c')
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('C5', None)

        >>> an = abcFormat.ABCNote("c'")
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('C5', None)

        >>> an = abcFormat.ABCNote('=c')
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('Cn5', True)

        >>> an = abcFormat.ABCNote("_c'")
        >>> n = an.m21Object()
        >>> n, n.pitch.accidental.displayStatus
        ('C-4', True)
        '''

        if self.isRest:
            return note.Rest()

        # Erstelle ein mapping für pitches die entweder in der signature oder in carry vorhanden sind
        # Übernehme das accidental für diese pitches aus carry, wenn nicht vorhanden aus der signature
        #signature = p.step : p.pitch for p in active_keySignature.alteredPitches }
        if self.carriedAccidental:
            active_accidental = self.carriedAccidental
        elif self.activeKeySignature:
            active_accidental = next( p.accidental.name for p in self.activeKeySignature.alteredPitches
                                      if p.step == self.pitch_name)
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

        pitchname = f'{self.pitch_name}{self.octave}{accidental}'

        from music21.note import Note

        try:
            n = Note(pitchname)
        except:
            raise ABCTokenException(f'Pitchname {pitchname} is not valid m21 syntax for a Note')

        if n.pitch.accidental is not None:
            n.pitch.accidental.displayStatus = display

        n.duration.quarterLength = self.quarterLength()
        # Apply accents to music21 note object
        self.apply_accents(n)
        # Apply articulations to musci21 note object
        self.apply_articulations(n)

        return n


class ABCChord(ABCGeneralNote):
    '''
    A representation of an ABC Chord, which contains within its delimiters individual notes.

    A subclass of ABCNote.
    '''

    def __init__(self, src:str, parent_handler: Optional['ABCHandler']=None, defaultQuarterLength: float=0.5):
        """
        argument:
            src:
                Token string of an abc note
            parent_handler:
                ABC Handler of the Chord
            carriedaccidental:
                m21 formatted accidental carried from a note of the same bar
            defaultQuarterLength:
                default length of an note an note with qan quarter note is 1.0
                (default value is according ABC standart 0.5 = 1/8)
        """
        intern, length = src.split(']', 1)
        super().__init__(src, length, defaultQuarterLength)
        self.innerStr = intern[1:]

        self._first_note: Optional[ABCNote] = None
        if parent_handler:
            self.chordHandler = ABCHandler(
                abcVersion=parent_handler.abcVersion,
                user_defined_token=parent_handler.user_defined_token
            )
        else:
            self.chordHandler = ABCHandler()

        pos = self.src.index(']')


    @property
    def subTokens(self):
        return self.chordHandler.tokens

    def isEmpty(self) -> bool:
        return self._first_note is None

    def tokenize(self):
        self.chordHandler.tokenize(self.innerStr)
        tokens = [t for t in self.chordHandler.tokens if
                  isinstance(t, (ABCAccent, ABCExpressions)) or isinstance(t, ABCNote) and not t.isRest]

        self._first_note = next(t for t in tokens if isinstance(t, ABCNote))
        self.chordHandler.tokens = tokens

    def quarterLength(self, defaultQuarterLength: Optional[float]=None):
        """
        >>> c = abcFormat.ABCChord('[abc]', defaultQuarterLength=1.0)
        >>> c.tokenize()
        >>> c.quarterLength()
        1.0

        >>> c = abcFormat.ABCChord('[abc]/2', defaultQuarterLength=1.0)
        >>> c.tokenize()
        >>> c.quarterLength()
        0.5

        >>> c = abcFormat.ABCChord('[e2fg]')
        >>> c.tokenize()
        >>> c.quarterLength()
        1.0

        >>> c = abcFormat.ABCChord('[ADF]3/2')
        >>> c.tokenize()
        >>> c.quarterLength()
        0.75

        >>> c = abcFormat.ABCChord('[ADF]//')
        >>> c.tokenize()
        >>> c.quarterLength()
        0.125

        >>> c = abcFormat.ABCChord('[A]///', defaultQuarterLength=1.0)
        >>> c.tokenize()
        >>> c.quarterLength()
        0.125
        """
        # <lengthModifier>: modifier providet by the abc length syntax
        # <brokenRyhtm>: a BrokenRythm modifier
        # <_first_note.quarterLength>: the length of the first note
        if self.isEmpty():
            return 0
        if defaultQuarterLength is None:
            defaultQuarterLength = self.activeDefaultQuarterLength
        return self.lengthModifier * self.brokenRyhtmModifier * self._first_note.quarterLength(defaultQuarterLength)


    def m21Object(self) -> 'music21.chord.Chort':
        notes = []
        for n in self.tokens:
            if isinstance(t, ABCNote):
                n.activeKeySignature = self.activeKeySignature
                n.defaultQuarterLength = self.defaultQuarterLength
                notes.append(n.m21Object())

        from music21.chord import Chord
        c = Chord(notes)
        c.duration.quarterLength = self.quarterLength()
        self.apply_accents(c)
        self.apply_articulations(c)
        return c

# ------------------------------------------------------------------------------
BARLINES = r"|".join([r':\|[12]?', r'[\|][\|\]:12]?', r'[\[][\|12]', r'[:][\|:]?'])
TOKEN_SPEC = {
    'DIRECTIVE': (r'^%%.*$', None),
    'COMMENT': ('[ ]*%.*$', None),
    'BARLINE': (BARLINES, ABCBar),
    'CHORD_SYMBOL': (r'"[^"]*"', ABCChordSymbol),
    #'LINE_CONTINUE': (r'[\\\\][ ]*$', None),
    'INLINE_FIELD': (r'\[[A-Zwms]:[^\]%]*\]', ABCMetadata),
    'TUPLET_GENERAL': (r'\([2-9]([:][2-9]?([:][2-9]?)?)?', ABCTuplet),
    'TUPLET_SIMPLE': (r'\([2-9]', ABCTuplet),
    'USER_DEF_FIELD': (r'[U]:[^|].*', None),
    'FIELD': (r'[A-Zmsrsw]:[^|].*(\n[+]:[^|].*)*', ABCMetadata),
    'CHORD': (r'[\[][^\]]*[\]][0-9]*[/]*[0-9]*', None),
    # 'CHORD_START': (r'\[', None),
    # 'CHORD_END': (r'\][0-9]*[/]*[1-9]*', None),
    'TIE': (r'-', ABCTie),
    'NOTE': (r'[\^_=]*[a-gA-GzZ][\',]*[0-9]*[/]*[0-9]*', ABCNote),
    'ACCENT': (r'[K]|!accent!', ABCAccent),
    'TRILL': (r'!trill!', ABCTrill),
    'UPBOW': (r'!upbow!', ABCUpbow),
    'DOWNBOW': (r'!downbow!', ABCDownbow),
    'FERMENTA': (r'!fermata!', ABCFermata),
    'IRISH_ROLL': (r'!roll!', None),  # Not Supported ?
    'LOWER_MORDENT': (r'!lowermordent!', ABCLowerMordent),
    'UPPER_MORDENT': (r'!uppermordent', ABCUpperMordent),
    'CODA': (r'!coda!', ABCCoda),
    'SEGNO': (r'!segno!', ABCSegno),
    'CRESENDO': (r'!(crescendo|<)[\(]!', ABCCrescStart),
    'DIMINUENDO': (r'!(diminuendo|>)[\(]!', ABCDimStart),
    'DIMINUENDO_STOP': (r'!(diminuendo|[>])[\)]!', ABCParenStop),
    'CRESENDO_STOP': (r'!(crescendo|[>])[\)]!', ABCParenStop),
    'SLUR': (r'\((?=[^0-9])', ABCSlurStart),
    'SLUR_STOP': (r'\)', ABCParenStop),
    'BROKEN_RYTHM': (r'[>]+|[<]+', ABCBrokenRhythmMarker),
    'GRACE': (r'{', ABCGraceStart),
    'GRACE_STOP': (r'}', ABCGraceStop),
    'STACCATO': (r'\.', ABCStaccato),
    'TENUTO': (r'M', ABCTenuto),
    'STRACCENT': (r'k', ABCStraccent),
    'USER_DEFINED_TOKEN': (r'[H-Wh-w~]', None),
    #'UNKNOWN_DECORATION': (r'![^!]+!', None),
    #'WHITESPACE': (r'[ ]+', None),
}

TOKEN_RE = re.compile(r'|'.join(r'(?P<%s>%s)' % (rule, v[0])
                                for rule, v in TOKEN_SPEC.items()),
                      re.MULTILINE)

class ABCHandler:
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

    def __init__(self, abcVersion=None, lineBreaksDefinePhrases=False, user_defined_token=None):

        # set explitcit ABCVersion, ignore version string in file
        self.abcVersion = abcVersion
        self.abcDirectives = {}
        self.user_defined_token = {} if user_defined_token is None else user_defined_token
        self.tokens = []
        self.activeParens = []
        self.activeSpanners = []
        self.lineBreaksDefinePhrases = lineBreaksDefinePhrases
        self.strSrc = ''
        self.srcLen = len(self.strSrc)  # just documenting this.'

    @staticmethod
    def barlineTokenFilter(token: str) -> List[ABCBar]:
        '''
        Some single barline tokens are better replaced
        with two tokens. This method, given a token,
        returns a list of tokens. If there is no change
        necessary, the provided token will be returned in the list.

        A staticmethod.  Call on the class itself.

        >>> abcFormat.ABCHandler.barlineTokenFilter('::')
        [<music21.abcFormat.ABCBar ':|'>, <music21.abcFormat.ABCBar '|:'>]

        >>> abcFormat.ABCHandler.barlineTokenFilter('|2')
        [<music21.abcFormat.ABCBar '|'>, <music21.abcFormat.ABCBar '[2'>]

        >>> abcFormat.ABCHandler.barlineTokenFilter(':|1')
        [<music21.abcFormat.ABCBar ':|'>, <music21.abcFormat.ABCBar '[1'>]

        If nothing matches, the original token is returned as an ABCBar object:

        >>> abcFormat.ABCHandler.barlineTokenFilter('hi')
        [<music21.abcFormat.ABCBar 'hi'>]
        '''
        return {
            '::': [ABCBar(':|'), ABCBar('|:')],
            '|1': [ABCBar('|'), ABCBar('[1')],
            '|2': [ABCBar('|'), ABCBar('[2')],
            ':|1': [ABCBar(':|'), ABCBar('[1')],
            ':|2': [ABCBar(':|'), ABCBar('[2')]
        }.get(token, [ABCBar(token)])

    # --------------------------------------------------------------------------
    # token processing

    def getUserdefinedToken(self, src: str) -> str:
        # get the token rule and the abc expression in the user_definition.
        # if the symbol is not defined in the user definitions
        # return the default token.
        DEFAULTS = {
            '~': 'IRISH_ROLL',
            'H': 'FERMENTA',
            'L': 'ACCENT',
            'M': 'LOWER_MORDENT',
            'O': 'CODA',
            'P': 'UPPER_MORDENT',
            'S': 'SEGNO',
            'T': 'TRILL',
            'u': 'UPBOW',
            'v': 'DOWNBOW'
        }
        try:
            return self.user_defined_token[src]
        except KeyError:
            return DEFAULTS[src]

    def _accidentalPropagation(self) -> str:
        '''
        Determine how accidentals should 'carry through the measure.'

        >>> ah = abcFormat.ABCHandler(abcVersion=(1, 3, 0))
        >>> ah._accidentalPropagation()
        'not'
        >>> ah = abcFormat.ABCHandler(abcVersion=(2, 0, 0))
        >>> ah._accidentalPropagation()
        'pitch'
        '''
        minVersion = (2, 0, 0)
        if not self.abcVersion or self.abcVersion < minVersion:
            return 'not'
        if 'propagate-accidentals' in self.abcDirectives:
            return self.abcDirectives['propagate-accidentals']
        return 'pitch'  # Default per abc 2.1 standard

    def parseABCVersion(self, src: str):
        '''
        Every abc file conforming to the standard should start with the line
        %abc-2.1

        >>> ah = abcFormat.ABCHandler()
        >>> ah.parseABCVersion('%abc-2.3.2')
        >>> ah.abcVersion
        (2, 3, 2)

        >>> ah = abcFormat.ABCHandler(abcVersion=(1, 3, 0))
        >>> ah.parseABCVersion('%abc-2.3.2')
        >>> ah.abcVersion
        (1, 3, 0)

        Catch only abc version as first comment line
        >>> ah = abcFormat.ABCHandler()
        >>> ah.parseABCVersion('%first comment\\n%abc-2.3.2')
        >>> ah.abcVersion

        But ignore post comments
        >>> ah = abcFormat.ABCHandler()
        >>> ah.parseABCVersion('X:1 % reference number\\n%abc-2.3.2')
        >>> ah.abcVersion
        (2, 3, 2)

        '''
        # ABCVersion has alredy set
        if self.abcVersion:
            return

        verMats = RE_ABC_VERSION.match(src)
        if verMats:
            abcMajor = int(verMats.group(3))
            abcMinor = int(verMats.group(4))
            if verMats.group(5):
                abcPatch = int(verMats.group(5))
            else:
                abcPatch = 0
            self.abcVersion =  (abcMajor, abcMinor, abcPatch)

    def tokenize(self, strSrc: str, user_defintions: Optional[Dict]=None, token_regex=TOKEN_RE) -> None:
        """
        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokens
        []
        >>> abch.tokenize('X: 1')
        >>> abch.tokens
        [<music21.abcFormat.ABCMetadata 'X: 1'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6f')
        >>> abch.tokens
        [<music21.abcFormat.ABCTuplet '(6'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6:4f')
        >>> abch.tokens
        [<music21.abcFormat.ABCTuplet '(6:4'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6:4:2f')
        >>> abch.tokens
        [<music21.abcFormat.ABCTuplet '(6:4:2'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6::2f')
        >>> abch.tokens
        [<music21.abcFormat.ABCTuplet '(6::2'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('TD')
        >>> abch.tokens
        [<music21.abcFormat.ABCTrill 'T'>, <music21.abcFormat.ABCNote 'D'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('U:T=!upbow!\\nTD')
        >>> abch.tokens
        [<music21.abcFormat.ABCUpbow 'T'>, <music21.abcFormat.ABCNote 'D'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('K: C % comment')
        >>> abch.tokens
        [<music21.abcFormat.ABCMetadata 'K: C'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('C: Tom\\n+:Waits')
        >>> abch.tokens
        [<music21.abcFormat.ABCMetadata 'C: Tom Waits'>]
        """
        if self.abcVersion is None:
            self.abcVersion = self.parseABCVersion(strSrc)

        self.strSrc = strSrc
        token_list = []

        # Dictonary for predefined symbols
        if user_defintions is None:
            user_defintions = {}

        for m in TOKEN_RE.finditer(strSrc):
            rule = m.lastgroup
            value = m.group()

            if rule == 'DIRECTIVE':
                directiveMatches = RE_DIRECTIVE.match(value)
                if directiveMatches:
                    directiveKey = directiveMatches.group(1)
                    directiveValue = directiveMatches.group(2)
                    self.abcDirectives[directiveKey] = directiveValue
                continue

            # Store a user defined symbol (Redefinable symbols)
            if rule == 'USER_DEF_FIELD':
                m = ABCMetadata(value)
                symbol, rule = m.getUserDefinedSymbol()
                if rule:
                    self.user_defined_token[symbol] = rule
                continue

            # Tokenize the internal abc string of a Chord
            if rule == 'CHORD':
                t = ABCChord(value, parent_handler=self)
                t.tokenize()
                token_list.append(t)
                continue

            # Lookup user defined token
            if rule == 'USER_DEFINED_TOKEN':
                try:
                    rule = self.getUserdefinedToken(value)
                except KeyError:
                    # Token is not user defined or has a default definition
                    pass

            # Some barlines are replaced by multiple tokens
            if rule == 'BARLINE':
                token_list.extend(self.barlineTokenFilter(value))
                continue

            # Lookup a ABCToken class for the rule and create the token
            regex, token_class = TOKEN_SPEC[rule]
            if token_class:
                try:
                    token = token_class(src=value)
                except Exception as e:
                    raise ABCHandlerException(f'Creating token [{token_class}] failed.\n{e}')
                token_list.append(token)
            else:
                environLocal.printDebug(
                    [f'No token class for rule "{rule}" with matching regex "{regex}"'])

        self.tokens = token_list

    def tokenProcess(self):
        '''
        Process all token objects.
        does context assignments, then calls parse().
        '''
        # need a key object to get altered pitches
        from music21 import key

        def preprocess(tokens: List[ABCToken]) -> Iterator[ABCToken]:
            '''
            Apply artikulations and expressions to note & chord tokens and
            remove them (do not yield).
            '''

            lastExpressions = []  # collection of expressions
            lastArticulations = []  # collection of articulation

            for token in tokens:
                if isinstance(token, ABCArticulation):
                    # Filter and collect articulation token
                    lastArticulations.append(token.m21Name)
                    continue

                elif isinstance(token, ABCExpressions):
                    # Filter and collect expression token
                    lastExpressions.append(token.m21Object())
                    continue

                if isinstance(token, ABCNote):
                    # Attached the collected articulations to notes
                    token.articulations = lastArticulations
                    lastArticulations = []

                    # Attached the collected expressions to notes
                    token.expressions = lastExpressions
                    lastExpressions = []

                yield token
        # context: iterate through tokens, supplying contextual data
        # as necessary to appropriate objects
        lastDefaultQL = 0.5     #  The default unit note length is 1/8.
        lastKeySignature = None
        lastTimeSignatureObj = None     # an m21 object
        lastTupletToken = None          # a token obj; keeps count of usage
        lastTieToken = None
        lastGraceToken = None
        lastNoteToken = None
        accidentalized = {}
        lastBrokenRythm = None
        note_token_stack = []
        tokens = self.tokens
        self.tokens = []

        # create 3 iterators over the same list
        #p, t, n = tee(filter_expression_and_articulations(tokens), 3)
        # for the tPrev interator chain a None in front of the tokens
        #p = chain([None], p)
        # for the tNext interator chain a None after the tokens
        #n = chain(islice(n, 1, None), [None])

        # Zip the iterators and start to iterate
        #iter_context = zip(p, t, n)

        # Parse first the complete Metadata from Header
        # The Header ends with the first 'K:' field or the first
        # Token not an instance of Metadata
        # The Header also ends and start with a new Header

        for t in preprocess(tokens):
            # Collect user defined token and replace it with the defined token
            if isinstance(t, ABCMetadata):
                if t.isMeter():
                    ts = t.getTimeSignatureObject()
                    if ts:
                        lastTimeSignatureObj = ts

                elif t.isDefaultNoteLength():
                    dl = t.getDefaultQuarterLength()
                    if dl:
                        lastDefaultQL = dl

                    # @TODO: remove this token, we don't need them any more
                    # continue

                elif t.isKey():
                    ks = t.getKeySignatureObject()
                    if ks:
                        lastKeySignature = ks

                elif t.isReferenceNumber():
                    # reset any spanners or parens at the end of any piece
                    # in case they aren't closed.
                    self.activeParens = []
                    self.activeSpanners = []

            # broken rhythms need to be applied to previous and next notes
            elif isinstance(t, ABCBrokenRhythmMarker):
                if lastNoteToken:
                    # add the lastNote as left side note to broken rythm the broken rythm
                    lastBrokenRythm = t

                # @TODO: remove this token, we don't need them any more
                # continue
            elif isinstance(t, ABCBar):
                # reset active accidentals in ABC Version > 2.1 only
                accidentalized = {}

            # need to update tuplets with currently active meter
            elif isinstance(t, ABCTuplet):
                t.updateRatio(lastTimeSignatureObj)
                # set number of notes that will be altered
                # might need to do this with ql values, or look ahead to nxt
                # token
                t.updateNoteCount()
                lastTupletToken = t
                self.activeParens.append('Tuplet')

            # notes within slur marks need to be added to the spanner
            elif isinstance(t, ABCSpanner):
                self.activeSpanners.append(t.m21Object)
                self.activeParens.append(t)

            elif isinstance(t, ABCParenStop):
                if self.activeParens:
                    p = self.activeParens.pop()
                    if isinstance(p, ABCSpanner):
                        self.activeSpanners.pop()

            elif isinstance(t, ABCTie):
                # tPrev is usually an ABCNote but may be a GraceStop.
                if lastNoteToken and lastNoteToken.tie == 'stop':
                    lastNoteToken.tie = 'continue'
                elif lastNoteToken:
                    lastNoteToken.tie = 'start'
                lastTieToken = t

            elif isinstance(t, ABCGraceStart):
                lastGraceToken = t

            elif isinstance(t, ABCGraceStop):
                lastGraceToken = None

            # ABCChord inherits ABCNote, thus getting note is enough for both
            elif isinstance(t, ABCGeneralNote):
                if isinstance(t, ABCChord):
                    # process the inner chord subtokens
                    # @TODO: is accidental propagation relevant for Chords ?
                    t.chordHandler.tokenProcess()

                if isinstance(t, ABCNote):
                    if not t.isRest:
                        propagation = self._accidentalPropagation()
                        if t.accidental:
                            # Remember the active accidentals in the measure
                            if propagation == 'octave':
                                accidentalized[t.pitchWithOctave] = t.accidental
                            elif propagation == 'pitch':
                                accidentalized[t.pitchClass] = t.accidental
                        else:
                            # RLookup the active accidentals in the measure
                            if propagation == 'pitch' and t.pitchClass in accidentalized:
                                t.carriedAccidental = accidentalized[t.pitchClass]
                            elif propagation == 'octave' and t.pitchWithOctave in accidentalized:
                                t.carriedAccidental = accidentalized[t.pitchWithOctave]

                if lastDefaultQL is None:
                    # @TODO: There is always an lastDefaultQL
                    raise ABCHandlerException(
                        'no active default note length provided for note processing. '
                        + f'tPrev: {tPrev}, t: {t}, tNext: {tNext}'
                    )

                t.activeDefaultQuarterLength = lastDefaultQL
                t.activeKeySignature = lastKeySignature
                t.applicableSpanners = self.activeSpanners[:]  # fast copy of a list

                # ends ties one note after t
                # hey begin
                if lastTieToken is not None:
                    t.tie = 'stop'
                    lastTieToken = None
                if lastBrokenRythm:
                    # add the note token as right side note to the BrokenRythm
                    lastBrokenRythm.set_notes(lastNoteToken, t)
                    lastBrokenRythm = None
                if lastGraceToken is not None:
                    t.inGrace = True
                if lastTupletToken is None:
                    pass
                elif lastTupletToken.noteCount == 0:
                    lastTupletToken = None  # clear, no longer needed
                else:
                    lastTupletToken.noteCount -= 1  # decrement
                    # add a reference to the note
                    t.activeTuplet = lastTupletToken.m21Object

                lastNoteToken = t

            # Re-Insert token to the token list
            self.tokens.append(t)

        # parse : call methods to set attributes and parse abc string
        for t in self.tokens:
            # environLocal.printDebug(['tokenProcess: calling parse()', t])
            t.parse()

    def process(self, strSrc: str) -> None:
        self.tokens = []
        self.tokenize(strSrc)
        self.tokenProcess()
        # return list of tokens; stored internally

    # --------------------------------------------------------------------------
    # access tokens

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

    # --------------------------------------------------------------------------
    # utility methods for post processing

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
        for i in range(len(self.tokens)):
            t = self.tokens[i]
            if isinstance(t, ABCMetadata):
                if t.isReferenceNumber():
                    count += 1
                    if count > 1:
                        return True
        return False

    def splitByReferenceNumber(self):
        # noinspection PyShadowingNames
        r'''
        Split tokens by reference numbers.

        Returns a dictionary of ABCHandler instances, where the reference number
        is used to access the music. If no reference numbers are defined,
        the tune is available under the dictionary entry None.


        >>> abcStr = 'X:5\nM:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||'
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
        <music21.abcFormat.ABCMetadata 'O: Irish'>
        >>> ahDict[6].tokens[0]
        <music21.abcFormat.ABCMetadata 'O: Irish'>
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')

        ahDict = {}

        # tokens in this list are prepended to all tunes:
        prependToAllList = []
        activeTokens = []
        currentABCHandler = None

        for i, t in enumerate(self.tokens):
            if isinstance(t, ABCMetadata) and t.isReferenceNumber():
                if currentABCHandler is not None:
                    currentABCHandler.tokens = activeTokens
                    activeTokens = []
                currentABCHandler = ABCHandler()
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

    def getReferenceNumber(self):
        '''
        If tokens are processed, get the first
        reference number defined.


        >>> abcStr = 'X:5\\nM:6/8\\nL:1/8\\nK:G\\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.getReferenceNumber()
        '5'
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')
        for t in self.tokens:
            if isinstance(t, ABCMetadata):
                if t.isReferenceNumber():
                    return t.data
        return None

    def definesMeasures(self):
        '''
        Returns True if this token structure defines Measures in a normal Measure form.
        Otherwise False


        >>> abcStr = ('M:6/8\\nL:1/8\\nK:G\\nV:1 name="Whistle" ' +
        ...     'snm="wh"\\nB3 A3 | G6 | B3 A3 | G6 ||\\nV:2 name="violin" ' +
        ...     'snm="v"\\nBdB AcA | GAG D3 | BdB AcA | GAG D6 ||\\nV:3 name="Bass" ' +
        ...     'snm="b" clef=bass\\nD3 D3 | D6 | D3 D3 | D6 ||')
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.definesMeasures()
        True

        >>> abcStr = 'M:6/8\\nL:1/8\\nK:G\\nB3 A3 G6 B3 A3 G6'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.definesMeasures()
        False
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')
        count = 0
        for i in range(len(self.tokens)):
            t = self.tokens[i]
            if isinstance(t, ABCBar):
                # must define at least 2 regular barlines
                # this leave out cases where only double bars are given
                if t.isRegular():
                    count += 1
                    # forcing the inclusion of two measures to count
                    if count >= 2:
                        return True
        return False

    def splitByVoice(self) -> List['ABCHandler']:
        # noinspection PyShadowingNames
        '''
        Given a processed token list, look for voices. If voices exist,
        split into parts: common metadata, then next voice, next voice, etc.

        Each part is returned as a ABCHandler instance.

        >>> abcStr = ('M:6/8\\nL:1/8\\nK:G\\nV:1 name="Whistle" ' +
        ...     'snm="wh"\\nB3 A3 | G6 | B3 A3 | G6 ||\\nV:2 name="violin" ' +
        ...     'snm="v"\\nBdB AcA | GAG D3 | BdB AcA | GAG D6 ||\\nV:3 name="Bass" ' +
        ...     'snm="b" clef=bass\\nD3 D3 | D6 | D3 D3 | D6 ||')
        >>> ah = abcFormat.ABCHandler()
        >>> ah.process(abcStr)
        >>> tokenColls = ah.splitByVoice()
        >>> tokenColls[0]
        <music21.abcFormat.ABCHandler object at 0x...>

        Common headers are first

        >>> [t.src for t in tokenColls[0].tokens]
        ['M:6/8', 'L:1/8', 'K:G']

        Then each voice

        >>> [t.src for t in tokenColls[1].tokens]
        ['V:1 name="Whistle" snm="wh"', 'B3', 'A3', '|', 'G6', '|', 'B3', 'A3', '|', 'G6', '||']
        >>> [t.src for t in tokenColls[2].tokens]
        ['V:2 name="violin" snm="v"', 'B', 'd', 'B', 'A', 'c', 'A', '|',
         'G', 'A', 'G', 'D3', '|', 'B', 'd', 'B', 'A', 'c', 'A', '|', 'G', 'A', 'G', 'D6', '||']
        >>> [t.src for t in tokenColls[3].tokens]
        ['V:3 name="Bass" snm="b" clef=bass', 'D3', 'D3', '|', 'D6', '|',
         'D3', 'D3', '|', 'D6', '||']

        Then later the metadata can be merged at the start of each voice...

        >>> mergedTokens = tokenColls[0] + tokenColls[1]
        >>> mergedTokens
        <music21.abcFormat.ABCHandler object at 0x...>
        >>> [t.src for t in mergedTokens.tokens]
        ['M:6/8', 'L:1/8', 'K:G', 'V:1 name="Whistle" snm="wh"',
         'B3', 'A3', '|', 'G6', '|', 'B3', 'A3', '|', 'G6', '||']
        '''
        # TODO: this procedure should also be responsible for
        #     breaking the passage into voice/lyric pairs

        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')

        voiceCount = 0
        pos = []
        for i in range(len(self.tokens)):
            t = self.tokens[i]
            if isinstance(t, ABCMetadata):
                if t.isVoice():
                    # if first char is a number
                    # can be V:3 name="Bass" snm="b" clef=bass
                    if t.data[0].isdigit():
                        pos.append(i)  # store position
                        voiceCount += 1

        abcHandlers = []
        # no voices, or definition of one voice, or use of V: field for
        # something else
        if voiceCount <= 1:
            ah = self.__class__()  # just making a copy
            ah.tokens = self.tokens
            abcHandlers.append(ah)
        # two or more voices
        else:
            # collect start and end pairs of split
            pairs = []
            pairs.append([0, pos[0]])
            i = pos[0]
            for x in range(1, len(pos)):
                j = pos[x]
                pairs.append([i, j])
                i = j
            # add last
            pairs.append([i, len(self)])

            for x, y in pairs:
                ah = self.__class__()
                ah.tokens = self.tokens[x:y]
                abcHandlers.append(ah)

        return abcHandlers

    @staticmethod
    def _buildMeasureBoundaryIndices(
        positionList: List[int],
        lastValidIndex: int
    ) -> List[List[int]]:
        '''
        Staticmethod

        Given a list of indices of a list marking the position of
        each barline or implied barline, and the last valid index,
        return a list of two-element lists, each indicating
        the start and positions of a measure.

        Here's an easy case that makes this method look worthless:

        >>> AH = abcFormat.ABCHandler
        >>> AH._buildMeasureBoundaryIndices([8, 12, 16], 20)
        [[0, 8], [8, 12], [12, 16], [16, 20]]

        But in this case, we need to see that 12 and 13 don't represent different measures but
        probably represent an end and new barline (repeat bar), etc.

        >>> AH._buildMeasureBoundaryIndices([8, 12, 13, 16], 20)
        [[0, 8], [8, 12], [13, 16], [16, 20]]

        Here 115 is both the last barline and the last index, so there is no [115, 115] entry.

        >>> bi = [9, 10, 16, 23, 29, 36, 42, 49, 56, 61, 62, 64, 70, 77, 84, 90, 96, 103, 110, 115]
        >>> AH._buildMeasureBoundaryIndices(bi, 115)
        [[0, 9], [10, 16], [16, 23], [23, 29], [29, 36], [36, 42], [42, 49], [49, 56], [56, 61],
         [62, 64], [64, 70], [70, 77], [77, 84], [84, 90], [90, 96],
         [96, 103], [103, 110], [110, 115]]

        '''
        # collect start and end pairs of split
        pairs = []
        # first chunk is metadata, as first token is probably not a bar
        pairs.append([0, positionList[0]])
        i = positionList[0]  # get first bar position stored
        # iterate through every other bar position (already have first)
        for x in range(1, len(positionList)):
            j = positionList[x]
            if j == i + 1:  # a span of one is skipped
                i = j
                continue
            pairs.append([i, j])
            i = j  # the end becomes the new start
        # add last valid index
        if i != lastValidIndex:
            pairs.append([i, lastValidIndex])
        # environLocal.printDebug(['splitByMeasure(); pairs pre filter', pairs])
        return pairs

    def splitByMeasure(self) -> List['ABCHandlerBar']:
        '''
        Divide a token list by Measures, also
        defining start and end bars of each Measure.

        If a component does not have notes, leave
        as an empty bar. This is often done with leading metadata.

        Returns a list of ABCHandlerBar instances.
        The first usually defines only Metadata

        TODO: Test and examples
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')

        abcBarHandlers = []
        barIndices = self.tokensToBarIndices()

        # barCount = 0  # not used
        # noteCount = 0  # not used

        # environLocal.printDebug(['splitByMeasure(); raw bar positions', barIndices])
        measureIndices = self._buildMeasureBoundaryIndices(barIndices, len(self) - 1)
        # for x, y in pairs:
        #     environLocal.printDebug(['boundary indices:', x, y])
        #     environLocal.printDebug(['    values at x, y', self.tokens[x], self.tokens[y]])

        # iterate through start and end pairs
        for x, y in measureIndices:
            ah = ABCHandlerBar()
            # this will get the first to last
            # shave of tokens if not needed
            xClip = x
            yClip = y

            # check if first is a bar; if so, assign and remove
            if isinstance(self.tokens[x], ABCBar):
                lbCandidate = self.tokens[x]
                # if we get an end repeat, probably already assigned this
                # in the last measure, so skip
                # environLocal.printDebug(['reading pairs, got token:', lbCandidate,
                #    'lbCandidate.barType', lbCandidate.barType,
                #    'lbCandidate.repeatForm', lbCandidate.repeatForm])
                # skip end repeats assigned (improperly) to the left
                if (lbCandidate.barType == 'repeat'
                    and lbCandidate.repeatForm == 'end'):
                    pass
                else:  # assign
                    ah.leftBarToken = lbCandidate
                    # environLocal.printDebug(['splitByMeasure(); assigning left bar token',
                    #                        lbCandidate])
                # always trim if we have a bar
                xClip = x + 1
                # ah.tokens = ah.tokens[1:]  # remove first, as not done above

            # if x boundary is metadata, do not include it (as it is likely in the previous
            # measure) unless it is at the beginning.
            elif x != 0 and isinstance(self.tokens[x], ABCMetadata):
                xClip = x + 1
            else:
                # if we find a note in the x-clip position, it is likely a pickup the
                # first note after metadata. this we keep, b/c it
                # should be part of this branch
                pass

            if y >= len(self):
                yTestIndex = len(self)
            else:
                yTestIndex = y

            if isinstance(self.tokens[yTestIndex], ABCBar):
                rbCandidate = self.tokens[yTestIndex]
                # if a start repeat, save it to be placed as a left barline
                if not (rbCandidate.barType == 'repeat'
                        and rbCandidate.repeatForm == 'start'):
                    # environLocal.printDebug(['splitByMeasure(); assigning right bar token',
                    #                             lbCandidate])
                    ah.rightBarToken = self.tokens[yTestIndex]
                # always trim if we have a bar
                # ah.tokens = ah.tokens[:-1]  # remove last
                yClip = y - 1
            # if y boundary is metadata, include it
            elif isinstance(self.tokens[yTestIndex], ABCMetadata):
                pass  # no change
            # if y position is a note/chord, and this is the last index,
            # must included it
            elif not (isinstance(self.tokens[yTestIndex], (ABCNote, ABCChord))
                      and yTestIndex == len(self.tokens) - 1):
                # if we find a note in the yClip position, it is likely
                # a pickup, the first note after metadata. we do not include this
                yClip = yTestIndex - 1

            # environLocal.printDebug(['clip boundaries: x,y', xClip, yClip])
            # boundaries are inclusive; need to add one here
            ah.tokens = self.tokens[xClip:yClip + 1]
            # after bar assign, if no bars known, reject
            if not ah:
                continue
            abcBarHandlers.append(ah)

        # for sub in abcBarHandlers:
        #     environLocal.printDebug(['concluded splitByMeasure:', sub,
        #            'leftBarToken', sub.leftBarToken, 'rightBarToken', sub.rightBarToken,
        #            'len(sub)', len(sub), 'sub.hasNotes()', sub.hasNotes()])
        #     for t in sub.tokens:
        #         print('\t', t)
        return abcBarHandlers

    def tokensToBarIndices(self) -> List[int]:
        '''
        Return a list of indices indicating which tokens in self.tokens are
        bar lines or the last piece of metadata before a note or chord.
        '''
        barIndices = []
        tNext = None
        for i, t in enumerate(self.tokens):
            try:
                tNext = self.tokens[i + 1]
            except IndexError:
                tNext = None

            # either we get a bar, or we just complete metadata and we
            # encounter a note (a pickup)
            if isinstance(t, ABCBar):  # or (barCount == 0 and noteCount > 0):
                # environLocal.printDebug(['splitByMeasure()', 'found bar', t])
                barIndices.append(i)  # store position
                # barCount += 1  # not used
            # case of end of metadata and start of notes in a pickup
            # tag the last metadata as the end
            elif (isinstance(t, ABCMetadata)
                  and tNext is not None
                  and isinstance(tNext, (ABCNote, ABCChord))):
                barIndices.append(i)  # store position

        return barIndices

    def hasNotes(self) -> bool:
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
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling')
        return any(isinstance(t, (ABCNote, ABCChord)) for t in self.tokens)

    def getTitle(self) -> Optional[str]:
        '''
        Get the first title tag. Used for testing.

        Requires tokens to have been processed.
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')
        return next((t.data for t in self.tokens if isinstance(t, ABCMetadata) and t.isTitle()), None)


class ABCHandlerBar(ABCHandler):
    '''
    A Handler specialized for storing bars. All left
    and right bars are collected and assigned to attributes.
    '''

    # divide elements of a character stream into objects and handle
    # store in a list, and pass global information to components

    def __init__(self):
        # tokens are ABC objects in a linear stream
        super().__init__()

        self.leftBarToken = None
        self.rightBarToken = None

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


def mergeLeadingMetaData(barHandlers: List[ABCHandlerBar]) -> List[ABCHandlerBar]:
    '''
    Given a list of ABCHandlerBar objects, return a list of ABCHandlerBar
    objects where leading metadata is merged, if possible,
    with the bar data following.

    This consolidates all metadata in bar-like entities.
    '''
    mCount = 0
    metadataPos = []  # store indices of handlers that are all metadata
    for i in range(len(barHandlers)):
        if barHandlers[i].hasNotes():
            mCount += 1
        else:
            metadataPos.append(i)
    # environLocal.printDebug(['mergeLeadingMetaData()',
    #                        'metadataPosList', metadataPos, 'mCount', mCount])
    # merge meta data into bars for processing
    mergedHandlers = []
    if mCount <= 1:  # if only one true measure, do not create measures
        ahb = ABCHandlerBar()
        for h in barHandlers:
            ahb += h  # concatenate all
        mergedHandlers.append(ahb)
    else:
        # when we have metadata, we need to pass its tokens with those
        # of the measure that follows it; if we have trailing meta data,
        # we can pass but do not create a measure
        i = 0
        while i < len(barHandlers):
            # if we find metadata and it is not the last valid index
            # merge into a single handler
            if i in metadataPos and i != len(barHandlers) - 1:
                mergedHandlers.append(barHandlers[i] + barHandlers[i + 1])
                i += 2
            else:
                mergedHandlers.append(barHandlers[i])
                i += 1

    return mergedHandlers


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

        handler = ABCHandler(abcVersion=self.abcVersion)
        # return the handler instance
        handler.process(strSrc)
        return handler


# ------------------------------------------------------------------------------
class Test(unittest.TestCase):

    def testTokenization(self):
        from music21.abcFormat import testFiles

        for (tf, countTokens, noteTokens, chordTokens) in [
            (testFiles.fyrareprisarn, 241, 152, 0),
            (testFiles.mysteryReel, 192, 153, 0),
            (testFiles.aleIsDear, 291, 206, 32),
            (testFiles.testPrimitive, 100, 75, 2),
            (testFiles.williamAndNancy, 127, 93, 0),
            (testFiles.morrisonsJig, 178, 137, 0),
        ]:

            handler = ABCHandler()
            handler.tokenize(tf)
            tokens = handler.tokens  # get private for testing
            # Fix the number of Tokens about the number of additional ChordSymbol
            chord_symbols = [cs for cs in tokens if isinstance(cs, ABCChordSymbol)]
            countTokens += len(chord_symbols)
            try:
                self.assertEqual(len(tokens), countTokens)
            except:
                raise Exception(tf)
            countNotes = 0
            countChords = 0
            for o in tokens:
                if isinstance(o, ABCChord):
                    countChords += 1
                elif isinstance(o, ABCNote):
                    countNotes += 1

            try:
                self.assertEqual(countNotes, noteTokens)
            except:
                raise Exception(tf)
            try:
                self.assertEqual(countChords, chordTokens)
            except:
                raise Exception(tf)

    def testTokenProcessMetadata(self):
        from music21.abcFormat import testFiles

        # noinspection SpellCheckingInspection
        for (tf, titleEncoded, meterEncoded, keyEncoded) in [
            (testFiles.fyrareprisarn, 'Fyrareprisarn', '3/4', 'F'),
            (testFiles.mysteryReel, 'Mystery Reel', 'C|', 'G'),
            (testFiles.aleIsDear, 'Ale is Dear, The', '4/4', 'D',),
            (testFiles.kitchGirl, 'Kitchen Girl', '4/4', 'D'),
            (testFiles.williamAndNancy, 'William and Nancy', '6/8', 'G'),
        ]:

            handler = ABCHandler()
            handler.tokenize(tf)
            handler.tokenProcess()

            tokens = handler.tokens  # get private for testing
            for t in tokens:
                if isinstance(t, ABCMetadata):
                    if t.tag == 'T':
                        self.assertEqual(t.data, titleEncoded)
                    elif t.tag == 'M':
                        self.assertEqual(t.data, meterEncoded)
                    elif t.tag == 'K':
                        self.assertEqual(t.data, keyEncoded)

    def testMultilineMetadata(self):
        jg = "C: Johann\n" \
        +"+:Gambolputty de von Ausfern-Schplenden-Schlitter-Crasscrembon" \
        +"-Fried-Digger-Dingel-Dangel-Dongel-Dungel-Burstein\n" \
        +"+:von Knacker-Trasher-Apple-Banger-Horowitz-Ticolensic-Grander-Knotty-Spelltinkel-"\
        +"Grandlich-Grumbelmeyer-Spelterwasser-Kurstlich-Himbeleisen-Bahnwagen-Gutenabend-"\
        +"Bitte-Ein-Nürnberger-Bratwurscht'l-Gespurtn-Mitz-Weimache-Luber-Hundsfut-Gumberaber-" \
        +"Schönendanker-Kalbsfleisch-Mittler-Aucher\n" \
        +"+:von Hautkopf of Ulm"
        handler = ABCHandler()
        handler.tokenize(jg)
        self.assertEqual(len(handler.tokens[0].data), 423)

    def testTokenProcess(self):
        from music21.abcFormat import testFiles

        for tf in [
            testFiles.fyrareprisarn,
            testFiles.mysteryReel,
            testFiles.aleIsDear,
            testFiles.testPrimitive,
            testFiles.kitchGirl,
            testFiles.williamAndNancy,
        ]:
            handler = ABCHandler()
            handler.tokenize(tf)
            handler.tokenProcess()

    def testNoteParse(self):
        from music21 import key

        n = ABCNote('c', activeKeySignature = key.KeySignature(3)).m21Object()
        self.assertEqual(n.nameWithOctave, 'C#5')
        self.assertEqual(n.pitch.accidental.displayStatus, False)

        n = ABCNote('c').m21Object()
        self.assertEqual(n.nameWithOctave, 'C5')

        n = ABCNote('^c').m21Object()
        self.assertEqual(n.nameWithOctave, 'C#5')
        self.assertEqual(n.pitch.accidental.displayStatus, True)

        n = ABCNote('B', activeKeySignature=key.KeySignature(-3)).m21Object()
        self.assertEqual(n.nameWithOctave, 'B-4')
        self.assertEqual(n.pitch.accidental.displayStatus, False)

        n = ABCNote('B').m21Object()
        self.assertEqual(n.nameWithOctave, 'B-4')

        n = ABCNote('_B').m21Object()
        self.assertEqual(n.nameWithOctave, 'B-4')
        self.assertEqual(n.pitch.accidental.displayStatus, True)

    def testSplitByMeasure(self):

        from music21.abcFormat import testFiles

        ah = ABCHandler()
        ah.process(testFiles.hectorTheHero)
        ahm = ah.splitByMeasure()

        for i, l, r in [(0, None, None),  # meta data
                        (2, '|:', '|'),
                        (3, '|', '|'),
                        (-2, '[1', ':|'),
                        (-1, '[2', '|'),
                        ]:
            # print('expecting', i, l, r, ahm[i].tokens)
            # print('have', ahm[i].leftBarToken, ahm[i].rightBarToken)
            # print()
            if l is None:
                self.assertEqual(ahm[i].leftBarToken, None)
            else:
                self.assertEqual(ahm[i].leftBarToken.src, l)

            if r is None:
                self.assertEqual(ahm[i].rightBarToken, None)
            else:
                self.assertEqual(ahm[i].rightBarToken.src, r)

        # for ahSub in ah.splitByMeasure():
        #     environLocal.printDebug(['split by measure:', ahSub.tokens])
        #     environLocal.printDebug(['leftBar:', ahSub.leftBarToken,
        #        'rightBar:', ahSub.rightBarToken, '\n'])

        ah = ABCHandler()
        ah.process(testFiles.theBeggerBoy)
        ahm = ah.splitByMeasure()

        for i, l, r in [(0, None, None),  # meta data
                        (1, None, '|'),
                        (-1, '||', None),  # trailing lyric meta data
                        ]:
            # print(i, l, r, ahm[i].tokens)
            if l is None:
                self.assertEqual(ahm[i].leftBarToken, None)
            else:
                self.assertEqual(ahm[i].leftBarToken.src, l)

            if r is None:
                self.assertEqual(ahm[i].rightBarToken, None)
            else:
                self.assertEqual(ahm[i].rightBarToken.src, r)

        # test a simple string with no bars
        ah = ABCHandler()
        ah.process('M:6/8\nL:1/8\nK:G\nc1D2')
        ahm = ah.splitByMeasure()

        for i, l, r in [(0, None, None),  # meta data
                        (-1, None, None),  # note data, but no bars
                        ]:
            # print(i, l, r, ahm[i].tokens)
            if l is None:
                self.assertEqual(ahm[i].leftBarToken, None)
            else:
                self.assertEqual(ahm[i].leftBarToken.src, l)

            if r is None:
                self.assertEqual(ahm[i].rightBarToken, None)
            else:
                self.assertEqual(ahm[i].rightBarToken.src, r)

    def testMergeLeadingMetaData(self):
        from music21.abcFormat import testFiles

        # a case of leading and trailing meta data
        ah = ABCHandler()
        ah.process(testFiles.theBeggerBoy)
        ahm = ah.splitByMeasure()

        self.assertEqual(len(ahm), 14)

        mergedHandlers = mergeLeadingMetaData(ahm)

        # after merging, one less handler as leading meta data is merged
        self.assertEqual(len(mergedHandlers), 13)
        # the last handler is all trailing metadata
        self.assertTrue(mergedHandlers[0].hasNotes())
        self.assertFalse(mergedHandlers[-1].hasNotes())
        self.assertTrue(mergedHandlers[-2].hasNotes())
        # these are all ABCHandlerBar instances with bars defined
        self.assertEqual(mergedHandlers[-2].rightBarToken.src, '||')

        # a case of only leading meta data
        ah = ABCHandler()
        ah.process(testFiles.theAleWifesDaughter)
        ahm = ah.splitByMeasure()

        self.assertEqual(len(ahm), 10)

        mergedHandlers = mergeLeadingMetaData(ahm)
        # after merging, one less handler as leading meta data is merged
        self.assertEqual(len(mergedHandlers), 10)
        # all handlers have notes
        self.assertTrue(mergedHandlers[0].hasNotes())
        self.assertTrue(mergedHandlers[-1].hasNotes())
        self.assertTrue(mergedHandlers[-2].hasNotes())
        # these are all ABCHandlerBar instances with bars defined
        self.assertEqual(mergedHandlers[-1].rightBarToken.src, '|]')

        # test a simple string with no bars
        ah = ABCHandler()
        ah.process('M:6/8\nL:1/8\nK:G\nc1D2')
        ahm = ah.splitByMeasure()

        # split by measure divides meta data
        self.assertEqual(len(ahm), 2)
        mergedHandlers = mergeLeadingMetaData(ahm)
        # after merging, meta data is merged back
        self.assertEqual(len(mergedHandlers), 1)
        # and it has notes
        self.assertTrue(mergedHandlers[0].hasNotes())

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
        self.assertEqual(ahs[5].getTitle(), 'The Begger Boy')  # tokens

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
        self.assertEqual(len(ah), 244)  # total tokens

        ahs = ah.splitByReferenceNumber()
        self.assertEqual(len(ahs), 3)
        self.assertEqual(sorted(list(ahs.keys())), [166, 167, 168])

        self.assertEqual(ahs[168].tokens[0].src, 'X:168')  # first is retained
        self.assertEqual(ahs[168].getTitle(), '168  The Castle Gate   (HJ)')
        self.assertEqual(len(ahs[168]), 89)  # tokens

        self.assertEqual(ahs[166].tokens[0].src, 'X:166')  # first is retained
        # noinspection SpellCheckingInspection
        self.assertEqual(ahs[166].getTitle(), '166  Valentine Jigg   (Pe)')
        self.assertEqual(len(ahs[166]), 67)  # tokens

        self.assertEqual(ahs[167].tokens[0].src, 'X:167')  # first is retained
        self.assertEqual(ahs[167].getTitle(), '167  The Dublin Jig     (HJ)')
        self.assertEqual(len(ahs[167]), 88)  # tokens

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
        self.assertEqual(len(ah), 84)

        fp = corpus.getWork('essenFolksong/han1')
        af = ABCFile()
        af.open(fp)
        ah = af.read(339)  # returns a parsed handler
        af.close()
        self.assertEqual(len(ah), 101)

    def testSlurs(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.slurTest)
        self.assertEqual(len(ah), 70)  # number of tokens

    def testTies(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.tieTest)
        self.assertEqual(len(ah), 73)  # number of tokens

    def testCresc(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.crescTest)
        self.assertEqual(len(ah), 75)
        tokens = ah.tokens
        i = 0
        for t in tokens:
            if isinstance(t, ABCCrescStart):
                i += 1
        self.assertEqual(i, 1)

    def testDim(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.dimTest)
        self.assertEqual(len(ah), 75)
        tokens = ah.tokens
        i = 0
        for t in tokens:
            if isinstance(t, ABCDimStart):
                i += 1
        self.assertEqual(i, 1)

    def testStaccato(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.staccTest)
        self.assertEqual(len(ah), 75)

    def testBow(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.bowTest)
        self.assertEqual(len(ah), 75)
        tokens = ah.tokens
        i = 0
        j = 0
        for t in tokens:
            if isinstance(t, ABCNote):
                if 'upbow' in t.articulations:
                    i += 1
                if 'downbow' in t.articulations:
                    j += 1
        self.assertEqual(i, 2)
        self.assertEqual(j, 1)

    def testAcc(self):
        from music21.abcFormat import testFiles
        from music21 import abcFormat
        ah = abcFormat.ABCHandler()
        ah.process(testFiles.accTest)
        # noinspection SpellCheckingInspection
        tokensCorrect = '''<music21.abcFormat.ABCMetadata 'X: 979'>
<music21.abcFormat.ABCMetadata 'T: Staccato test, plus accents and tenuto marks'>
<music21.abcFormat.ABCMetadata 'M: 2/4'>
<music21.abcFormat.ABCMetadata 'L: 1/16'>
<music21.abcFormat.ABCMetadata 'K: Edor'>
<music21.abcFormat.ABCNote 'B,2'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCDimStart '!diminuendo(!'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote '^D'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCTie '-'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCParenStop '!diminuendo)!'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCTuplet '(3'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'B'>
<music21.abcFormat.ABCNote 'A'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote '^D'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCTuplet '(3'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCTie '-'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'B'>
<music21.abcFormat.ABCNote 'A'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCNote '^D'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCTuplet '(3'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'A'>
<music21.abcFormat.ABCTie '-'>
<music21.abcFormat.ABCNote 'A'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote '^D'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCTuplet '(3'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'B'>
<music21.abcFormat.ABCNote 'A'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCNote 'G6'>
'''.splitlines()
        tokensReceived = [str(x) for x in ah.tokens]
        self.assertEqual(tokensCorrect, tokensReceived)

        self.assertEqual(len(ah), 75)
        tokens = ah.tokens
        i = 0
        j = 0
        k = 0
        for t in tokens:
            if isinstance(t, music21.abcFormat.ABCNote):
                if 'accent' in t.articulations:
                    i += 1
                if 'strongaccent' in t.articulations:
                    j += 1
                if 'tenuto' in t.articulations:
                    k += 1

        self.assertEqual(i, 2)
        self.assertEqual(j, 2)
        self.assertEqual(k, 2)

    def testGrace(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.graceTest)
        self.assertEqual(len(ah), 85)

    def testGuineaPig(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.guineapigTest)
        self.assertEqual(len(ah), 94)


# ------------------------------------------------------------------------------
# define presented order in documentation
_DOC_ORDER = [ABCFile, ABCHandler, ABCHandlerBar]

if __name__ == '__main__':
    # sys.arg test options will be used in mainTest()
    import music21

    music21.mainTest(Test)

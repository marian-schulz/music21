# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
# Name:         abc/tokens.py
# Purpose:      tokenzie ABC Notation
#
# Authors:      Christopher Ariza
#               Dylan J. Nagler
#               Michael Scott Cuthbert
#
# Copyright:    Copyright © 2010, 2013 Michael Scott Cuthbert and the music21 Project
# License:      BSD, see license.txt
# ------------------------------------------------------------------------------

import copy
import io
import re
import unittest
from typing import Union, Optional, List, Tuple, Iterable, Type

from music21 import common
from music21 import environment
from music21 import exceptions21
from music21 import dynamics
from music21 import expressions
from music21 import articulations
from music21 import duration
from music21 import spanner
from music21 import key
from music21 import repeat
from music21 import chord
from music21 import note

environLocal = environment.Environment('abcFormat')
environment.set('debug', True)


RE_ABC_NOTE = re.compile(r'([\^_=]*)([A-Ga-gz])([0-9/\',]*)')
RE_ABC_LYRIC = re.compile(r'[^*\-_ ]+[-]?|[*\-_]')


class ABCTokenException(exceptions21.Music21Exception):
    pass

RE_ABC_VERSION = re.compile(r'(?:((^[^%].*)?[\n])*%abc-)(\d+)\.(\d+)\.?(\d+)?')


def parseABCVersion(src: str) -> Optional[Tuple[int,int,int]]:
    '''
    Every abc file conforming to the standard should start with the line
    %abc-2.1

    >>> ah.parseABCVersion('%abc-2.3.2')
    (2, 3, 2)

    Catch only abc version as first comment line
    >>> ah.parseABCVersion('%first comment\\n%abc-2.3.2')

    But ignore post comments
    >>> ah.parseABCVersion('X:1 % reference number\\n%abc-2.3.2')
    (2, 3, 2)
    '''
    verMats = RE_ABC_VERSION.match(src)
    if verMats:
        abcMajor = int(verMats.group(3))
        abcMinor = int(verMats.group(4))
        abcPatch = int(verMats.group(5)) if verMats.group(5) else 0
        return (abcMajor, abcMinor, abcPatch)


class ABCToken():
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

    def __init__(self, src: str):
        self.src: str = src  # store source character sequence

    def m21Object(self):
        return None

    def __str__(self):
        return f"<self.__class__.__name__ '{self.src}'>"

    def __repr__(self):
        return f"<{self.__class__.__name__} '{self.src}'>"



class ABCMark(ABCToken):
    '''
    Base class of abc score marker token
    Marker can placed on every position in a stream.
    '''


class ABCArticulation(ABCToken):
    '''
    Baseclass of articulation tokens.
    ABCArticulations precede a note or chord,
    they are a property of that note/chord.
    '''
    def __init__(self, src=''):
        super().__init__(src)

    def m21Object(self) -> articulations.Articulation:
        return None


class ABCExpression(ABCToken)  :
    '''
    Base class of abc note expresssion token
    '''
    def __init__(self, src=''):
        super().__init__(src)

    def m21Object(self) -> expressions.Expression:
        return None


class ABCDirective(ABCMark):
    """
    Dynamic mark
    """
    REGEX = r'^%%.*$'
    RE_PARSE = re.compile(r'^%%([a-z\-]+)\s+([^\s]+)(.*)').match

    def __init__(self):
        super().__init__(src[2:])
        m = ABCDirective.RE_PARSE(value)
        if directiveMatches:
            self.Key = directiveMatches.group(1)
            self.Value = directiveMatches.group(2)
            self.abcDirectives[directiveKey] = directiveValue
        else:
            raise ABCTokenException('Invalid ABC Directive: "<{self.src}>"')


class ABCField(ABCToken):
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
    REGEX = r'^[A-Zmsrsw]:[^|].*(\n[+]:[^|].*)*'

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

    def isSymbolLine(self) -> bool:
        '''
        Returns True if the tag is "Q" for tempo, False otherwise.
        '''
        return self.tag == 's'

    def getVoiceId(self) -> str:
        try:
            vid, _ = self.data.split(' ', 1)
        except:
            return self.data

        return vid

    def getSymbolLine(self) -> List[str]:
        '''
        >>> am = abcFormat.ABCMetadata('w:Si- - - - - - - cut ro *  -  -  sa')
        >>> am.getLyric()
        ['Si-', '-', '-', '-', '-', '-', '-', 'cut', 'ro', '*', '-', '-', 'sa']
        >>> am = abcFormat.ABCMetadata('w:Ha-ho')
        >>> am.getLyric()
        ['Ha-', 'ho']
        '''
        return [s.strip() for s in RE_ABC_LYRIC.findall(self.data)]

    def getLyric(self) -> List[str]:
        '''
        >>> am = abcFormat.ABCMetadata('w:Si- - - - - - - cut ro *  -  -  sa')
        >>> am.getLyric()
        ['Si-', '-', '-', '-', '-', '-', '-', 'cut', 'ro', '*', '-', '-', 'sa']
        >>> am = abcFormat.ABCMetadata('w:Ha-ho')
        >>> am.getLyric()
        ['Ha-', 'ho']
        '''
        return [s.strip() for s in RE_ABC_LYRIC.findall(self.data)]

    def getUserDefinedSymbol(self) -> Tuple[str, Optional[str]]:
        '''
        >>> am = abcFormat.ABCMetadata('U:Z=!trill!')
        >>> am.getUserDefinedSymbol()
        ('Z', 'ABCTrill')
        >>> am = abcFormat.ABCMetadata('U:Z=!garbage!')
        >>> am.getUserDefinedSymbol()
        ('Z', None)
        '''
        if not (self.isUserDefinedSymbol()):
            raise ABCTokenException(
                'no user defined symbol is associated with this metadata.')

        symbol, definition = self.data.split('=', 1)
        try:
            m = TOKEN_RE.match(definition)
            r = symbol, m.lastgroup
            return r
        except AttributeError:
            return (symbol, None)

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
            raise ABCTokenException(
                f'no quarter length associated with this metadata: {self.data}')


class ABCInlineField(ABCField):

    REGEX = r'\[[A-Zwms]:[^\]%]*\]'

    def __init__(self, src: str):
        super().__init__(src[1:-1])

        if self.tag not in 'IKLMmNPQRrUV':
            raise ABCTokenException(f'Field tag "{self.tag}" cannot inlined.')

class ABCBar(ABCToken):

    ABC_BARS = {
        ':|1': 'light-heavy-repeat-end-first',
        ':|2': 'light-heavy-repeat-end-second',
        '|]': 'light-heavy',
        '||': 'light-light',
        '[|': 'heavy-light',
        '[1': 'regular-first',  # preferred format
        '[2': 'regular-second',
        '|1': 'regular-first',  # gets converted
        '|2': 'regular-second',
        ':|': 'light-heavy-repeat-end',
        '|:': 'heavy-light-repeat-start',
        '::': 'heavy-heavy-repeat-bidirectional',
        '|': 'regular',
        ':': 'dotted',
    }
    REGEX = r"|".join([r':\|[12]?', r'[\|][\|\]:12]?', r'[\[][\|12]', r'[:][\|:]?'])

    def __init__(self, src: str):
        super().__init__(src.strip())
        barTypeComponents = ABCBar.ABC_BARS.get(self.src,'').split('-')
        self.barType = 'repeat' if 'repeat' in barTypeComponents else 'barline'
        self.repeatForm = None
        if len(barTypeComponents) == 1:
            self.barStyle = barTypeComponents[0]
        else:
            # case of light-heavy, light-light, etc
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

    @classmethod
    def barlineTokenFilter(cls, token: str) -> List['ABCBar']:
        '''
        Some single barline tokens are better replaced
        with two tokens. This method, given a token,
        returns a list of tokens. If there is no change
        necessary, the provided token will be returned in the list.

        A classmethod  Call on the class itself.

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
        return {
            '::': [cls(':|'), cls('|:')],
            '|1': [cls('|'), cls('[1')],
            '|2': [cls('|'), cls('[2')],
            ':|1': [cls(':|'), cls('[1')],
            ':|2': [cls(':|'), cls('[2')]
        }.get(token, [cls(token)])

    def isRepeat(self) -> bool:
        '''Is a repeat bar'''
        return self.barType == 'repeat'

    def isRegular(self) -> bool:
        '''Is a regular bar'''
        return self.barType != 'repeat' and self.barStyle == 'regular'

    def isRepeatBracket(self) -> Union[int, bool]:
        return {'first': 1, 'second': 2}.get(self.repeatForm, False)

    def ms21Object(self) -> Optional['music21.bar.Barline']:
        ''' create a music21 barline object

        Returns:
            A music21 bar object

        >>> ab = abcFormat.ABCBar('|:')
        >>> barObject = ab.ms21Object()
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

    REGEX = r'\([2-9]|\([2-9]([:][2-9]?([:][2-9]?)?)?'

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
        self._m21Object = None

    def m21Object(self) -> Optional[duration.Tuplet]:
        return self._m21Object

    def updateRatio(self, keySig: Optional[key.KeySignature]=None):
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
        if keySig is None or keySig.beatDivisionCount != 3:
            normalSwitch = 2  # 4/4
        else:  # if compound
            normalSwitch = 3

        splitTuplet = self.src.strip().split(':')
        tupletNumber = int(splitTuplet[0][1:])

        if 1 <= tupletNumber <= 9:
            if len(splitTuplet) >= 2 and splitTuplet[1]:
                self.numberNotesNormal = int(splitTuplet[1])
            else:
                self.numberNotesNormal = {1: 1,
                                          2: 3,
                                          4: 3,
                                          6: 2,
                                          8: 3}.get(tupletNumber, normalSwitch)
            self.numberNotesActual = tupletNumber
        else:
            raise ABCTokenException(f"cannot handle tuplet of form: '({tupletNumber!r}'")


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
        >>> at.m21Object()
        <music21.duration.Tuplet 6/2>

        >>> at = abcFormat.ABCTuplet('(6:4:12')
        >>> at.updateRatio()
        >>> at.updateNoteCount()
        >>> at.noteCount
        12
        >>> at.m21Object()
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
        self._m21Object = duration.Tuplet(
            numberNotesActual=self.numberNotesActual,
            numberNotesNormal=self.numberNotesNormal)

        # copy value; this will be dynamically counted down
        splitTuplet = self.src.strip().split(':')
        if len(splitTuplet) >= 3 and splitTuplet[2]:
            self.noteCount = int(splitTuplet[2])
        else:
            self.noteCount = self.numberNotesActual

        # self.qlRemain = self._tupletObj.totalTupletLength()


class ABCSpanner(ABCToken):
    def __init__(self, src):
        super().__init__(src)

    def m21Object(self):
        return None


class ABCDynamic(ABCMark):
    """
    Dynamic mark
    """
    REGEX = r'![p]{1,4}!|![f]{1,4}!|!m[pf]!|!sfz!'

    def m21Object(self) -> dynamics.Dynamic:
        return dynamics.Dynamic(self.src)


class ABCTie(ABCToken):
    '''
    Handles instances of ties '-' between notes in an ABC score.
    Ties are treated as an attribute of the note before the '-';
    the note after is marked as the end of the tie.
    '''
    REGEX = r'-'

    def __init__(self, src):
        super().__init__(src)


class ABCSlurStart(ABCSpanner):
    '''
    ABCSlurStart tokens always precede the notes in a slur.
    For nested slurs, each open parenthesis gets its own token.
    '''

    REGEX_START = r'\((?=[^0-9])'
    REGEX_STOP =  r'\)'

    def __init__(self, src: str):
        super().__init__(src)

    def m21Object(self) -> spanner.Slur:
        from music21.spanner import Slur
        return Slur()


class ABCParenStop(ABCToken):
    '''
    A general parenthesis stop;
    comes at the end of a tuplet, slur, or dynamic marking.
    '''


class ABCCrescStart(ABCSpanner):
    '''
    ABCCrescStart tokens always precede the notes in a crescendo.
    the closing string "!crescendo)" counts as an ABCParenStop.
    '''

    REGEX_START = r'!(crescendo|<)[\(]!'
    REGEX_STOP = r'!(crescendo|[>])[\)]!'

    def __init__(self, src: str):
        super().__init__(src)

    def m21Object(self) -> dynamics.Crescendo:
        '''
        Create a music21 cressendo (dynamics)

        Returns:
             a music21 Cressendo object
        '''
        return dynamics.Crescendo()


class ABCDimonuendo(ABCSpanner):
    '''
    ABCDimStart tokens always precede the notes in a diminuendo.
    '''

    REGEX_START = r'!(diminuendo|>)[\(]!'
    REGEX_STOP = r'!(diminuendo|[>])[\)]!'

    def __init__(self, src: str):
        super().__init__(src)

    def m21Object(self) -> dynamics.Diminuendo:
        '''
        Create a musci21 diminuendo (dynamics)

        Returns:
             a music21 Diminuendo object
        '''
        return Diminuendo()


class ABCStaccato(ABCArticulation):
    '''
    ABCStaccato tokens precede a note or chord.
    they are a property of that note/chord.
    '''
    REGEX = '[\.]'

    def m21Object(self) -> articulations.Staccato:
        from music21.articulations import Staccato
        return Staccato()


class ABCUpbow(ABCArticulation):
    '''
    ABCUpbow tokens precede a note or chord;
    they are a property of that note/chord.
    '''
    REGEX = "!upbow!"
    def m21Object(self) -> articulations.UpBow:
        from music21.articulations import UpBow
        return UpBow()


class ABCDownbow(ABCArticulation):
    '''
    ABCDowmbow tokens precede a note or chord;
    they are a property of that note/chord.
    '''
    REGEX = "!downbow!"
    def m21Object(self) -> articulations.DownBow:
        from music21.articulations import DownBow
        return DownBow()


class ABCAccent(ABCArticulation):
    '''
    ABCAccent tokens "K" precede a note or chord;
    they are a property of that note/chord.
    These appear as ">" in the output.
    '''
    REGEX = "!accent!|!>!|!emphasis!"
    def m21Object(self) -> articulations.Accent:
        from music21.articulations import Accent
        return Accent()


class ABCStraccent(ABCArticulation):
    '''
    ABCStraccent tokens "k" precede a note or chord;
    they are a property of that note/chord.
    These appear as "^" in the output.

    @TODO: Cannot find this !straccent not 'k' in ABC dokumentation
    '''
    # regular expression
    REGEX = r'!straccent!'

    def m21Object(self) -> articulations.StrongAccent:
        from music21.articulations import StrongAccent
        return StrongAccent()


class ABCTenuto(ABCArticulation):
    '''
    ABCTenuto tokens "M" precede a note or chord;
    they are a property of that note/chord.
    '''
    # regular expression
    REGEX = r'!tenuto!'

    def m21Object(self) -> articulations.Tenuto:
        from music21.articulations import Tenuto
        return Tenuto()


class ABCGraceStart(ABCToken):
    '''
    Grace note start
    '''
    REGEX = r'{'


class ABCGraceStop(ABCToken):
    '''
    Grace note end
    '''
    REGEX = r'}'

class ABCTrill(ABCExpression):
    '''
    Trill
    '''
    # regular expression
    REGEX= '!trill!'

    def m21Object(self) -> expressions.Trill:
        return expressions.Trill()


class ABCRoll(ABCToken):
    '''
    Irish Roll (Not Supported ?)
    '''
    pass


class ABCFermata(ABCExpression):
    '''
    Fermata expression
    '''
    REGEX = 'r!fermata!'

    def m21Object(self) -> expressions.Fermata:
        from music21.expressions import Expression
        return Fermata()


class ABCLowerMordent(ABCExpression):
    '''
    Lower mordent is a single rapid alternation with
    the note below
    '''
    REGEX = r'!lowermordent!|!mordent!'

    def m21Object(self) -> expressions.Mordent:
        mordent = expressions.Mordent()
        mordent.direction = 'down'
        return mordent


class ABCUpperMordent(ABCExpression):
    '''
    Upper mordent is a single rapid alternation with
    the note above
    '''

    REGEX = r'!uppermordent!|!pralltriller!'

    def m21Object(self) -> expressions.Mordent:
        mordent = expressions.Mordent()
        mordent.direction = 'up'
        return mordent


class ABCCoda(ABCMark):
    '''
    Coda score expression marker
    '''
    # token matched by this regular expression
    REGEX = r'!coda!'

    def m21Object(self) -> repeat.Coda:
        from music21 import repeat
        return repeat.Coda()


class ABCSegno(ABCMark):
    '''
    Segno score expressiion marker
    '''
    # token matched by this regular expression
    REGEX = r'!segno!'

    def m21Object(self) -> 'music21.repeat.Segno':
        """
        return:
            music21 object corresponding to the token
        """
        from music21 import repeat
        return repeat.Segno()


class ABCSymbol(ABCToken):
    """
        Redefinable symbols '[H-Wh-w~]'
    """
    REGEX = r'[H-Wh-w~]'

    DEFAULTS = {
        # '~': 'ABCIrishRoll',
        'H': 'ABCFermata',
        'L': 'ABCAccent',
        'M': 'ABCLowerMordent',
        'O': 'ABCCode',
        'P': 'ABCUpperMordent',
        'S': 'ABCSegno',
        'T': 'ABCTrill',
        'k': 'ABCStraccent',  # Not in recent ABC Standart ?!
        'K': 'ABCAccent',  # Nor this
        'u': 'ABCUpbow',
        'v': 'ABCDownbow'
    }

    def __init__(self, src: str):
        super().__init__(src)
        self.symbols = Optional[Dict: str, ABCToken]

    def __getitem__(self, key):
        return self.record.get(self.src, ABCSymbol.DEFAULTS['DEFAULTS'])


class ABCBrokenRhythm(ABCToken):
    '''
    Brokenrhythm is binary operator for two chord or note token to the
    left and right hand side of the BrokenRythm.
    It decreases the length of one note by a factor and increases the length of
    the second note by the inverse of the factor.

    Assign the two notes with set_notes, the brokenRythmModifier property of the
    note/chord is set.
    '''

    REGEX = r'[>]+|[<]+'

    def __init__(self, src: str):
        super().__init__(src)
        self.left, self.right = {'>': (1.5, 0.5), '>>': (1.75, 0.25),
                                 '>>>': (1.875, 0.125), '<': (0.5, 1.5),
                                 '<<': (0.25, 1.75), '<<<': (0.125, 1.875)
                                 }.get(self.src, (1, 1))


    def set_notes(self, left: 'ABCGeneralNote', right: 'ABCGeneralNote'):
        '''
        Set note the length modifier of the the BrokenRythm to the left
        and right node.

        Arguments:
            left:
                Note token to the left of the BrokenRythmMarker
            right:
                Note token to the right of the BrokenRythmMarker
        '''
        left.brokenRyhtmModifier = self.left
        right.brokenRyhtmModifier = self.right


class ABCChordSymbol(ABCMark):
    '''
    A chord symbol
    '''
    REGEX = r'"[^"]*"'

    def __init__(self, src):
        src = src[1:-1].strip()
        src = re.sub('[()]', '', src)
        super().__init__(src)

    def m21Object(self):
        from music21 import harmony
        cs_name = common.cleanedFlatNotation(self.src)
        try:
            if cs_name in ('NC', 'N.C.', 'No Chord', 'None'):
                cs = harmony.NoChord(cs_name)
            else:
                cs = harmony.ChordSymbol(cs_name)
            return cs
        except ValueError:
            return None


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
        self.inBeam = None      # @TODO: not implemented yet
        self.inGrace = None

        # store a tuplet if active
        self.activeTuplet = None

        # store a spanner if active
        self.applicableSpanners = []

        # store a tie if active
        self.tie = None

        # store ABCArticulation
        self.articulations: List[ABCArticulation]  = []
        # store ABCExpression
        self.expressions: List[ABCExpression] = []

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
        '''
        Parse a abc length string.

        argument:
            src:
                abc note/chord length string
                the function expects only numbers and the slash ('/') to be passed to it.
        return:
                length modifier als float.
        '''
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

    def quarterLength(self, defaultQuarterLength: Optional[float]=None):
        """
        Returns the length of this note/chort relative to a quarter note.
        Arguments:
            defaultQuarterLength:
                Optionally, a different DefaultQuarterLength can be specified.
                The quarter note length of the object is not replaced.
        Returns:
            The relative length of this

        >>> abcFormat.ABCNote('=c/2', defaultQuarterLength=0.5).quarterLength()
        0.25

        >>> abcFormat.ABCNote('e2', defaultQuarterLength=0.5).quarterLength()
        1.0

        >>> abcFormat.ABCNote('e2').quarterLength()
        1.0

        >>> abcFormat.ABCNote('A3/2').quarterLength()
        0.75

        >>> abcFormat.ABCNote('A//').quarterLength()
        0.125

        >>> abcFormat.ABCNote('A///').quarterLength()
        0.0625
        """
        if defaultQuarterLength is None:
            defaultQuarterLength = self.activeDefaultQuarterLength
        return self.lengthModifier * self.brokenRyhtmModifier * defaultQuarterLength

    def m21Object(self):
        """
        return:
            music21 object corresponding to the token.
            Needs implementation of the subclasses
        """
        return None

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
                    note.articulations.append(obj)
            except:
                environLocal.printDebug(
                    [f'Create music21 articulation object for Token: "{e.__class__.__name__}" failed.']
                )

    def apply_spanners(self, obj: 'music21.note.GeneralNote'):
        """
        Add collected spanner to a node/chord object.
        Arguments:
          obj:
            Add spanner to this music21 Note/Chord Object
        """
        for span in self.applicableSpanners:
            span.addSpannedElements(obj)

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
                if obj:
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

    REGEX = r'[\^_=]*[a-gA-GzZ][\',]*[0-9]*[/]*[0-9]*'

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
        self.accidental: str = a        # m21 formated accidental
        self.octave: int = o            # octave number

        # accidental propagated from a previous note in the same measure
        self.carriedAccidental: str = carriedAccidental

        # Note is a rest
        self.isRest: bool = self.pitch_name == 'Z'


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

        >>> abcFormat.ABCNote._parse_note('C')
        ('C', '', 4, '')

        >>> abcFormat.ABCNote._parse_note('^c')
        ('C', '#', 5, '')

        >>> abcFormat.ABCNote._parse_note('_c,,')
        ('C', '-', 3, '')

        >>> abcFormat.ABCNote._parse_note("__g'/4")
        ('G', '--', 6, '/4')

        >>> abcFormat.ABCNote._parse_note("^_C'6/4")
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

    def apply_tie(self, note: 'musci21.note.Note'):
        from music21 import tie
        if self.tie is not None:
            if self.tie in ('start', 'continue'):
                note.tie = tie.Tie(self.tie)
                note.tie.style = 'normal'
            elif self.tie == 'stop':
                note.tie = tie.Tie(self.tie)

    def m21Object(self) -> Union['music21.note.Note', 'music21.note.Rest']:
        """
            Get a music21 note or restz object
            QuarterLength, ties, articulations, expressions, grace,
            spanners and tuplets are applied.
            If this note is a rest, only tuplets, quarterlength and
            spanners are applied.

        return:
            music21 note or rest object corresponding to this token.

        >>> k = key.Key('G')
        >>> n = abcFormat.ABCNote("^f'", activeKeySignature=k).m21Object()
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
        if self.isRest:
            n =  note.Rest()
        else:
            # Erstelle ein mapping für pitches die entweder in der signature oder in carry vorhanden sind
            # Übernehme das accidental für diese pitches aus carry, wenn nicht vorhanden aus der signature
            #signature = p.step : p.pitch for p in active_keySignature.alteredPitches }
            if self.carriedAccidental:
                active_accidental = self.carriedAccidental
            elif self.activeKeySignature:
                active_accidental = next( (p.accidental.modifier for p in self.activeKeySignature.alteredPitches
                                          if p.step == self.pitch_name), None)
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

            try:
                n = note.Note(pitchname)
            except:
                raise ABCTokenException(f'Pitchname {pitchname} is not valid m21 syntax for a Note')

            if n.pitch.accidental is not None:
                n.pitch.accidental.displayStatus = display

            self.apply_articulations(n)
            self.apply_expressions(n)
            self.apply_spanners(n)

            if self.inGrace:
                n = n.getGrace()

        n.duration.quarterLength = self.quarterLength()
        self.apply_tuplet(n)
        self.apply_tie(n)
        return n


class ABCChord(ABCGeneralNote):
    '''
    A representation of an ABC Chord, which contains within its delimiters individual notes.

    A subclass of ABCNote.
    '''

    # Regular expression matching an ABCHord token
    REGEX = r'[\[][^\]]*[\]][0-9]*[/]*[0-9]*'

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
        self.innerStr: str = intern[1:]
        self._first_note: Optional[ABCNote] = None
        self.subTokens: List[Union[ABCNote, ABCExpression, ABCArticulation]] = []

    @property
    def isEmpty(self) -> bool:
        """
        A chord without a note is empty even if tokens
        of other types are present.
        """
        return self._first_note is None

    def tokenize(self):
        """
        Lets the chord handler tokenize the internal string.
        Illegal tokens are removed from the result.
        In addition, the first note of the chord is determined
        """

        # Only keep articulations, expressions and notes
        self.subTokens = [ t for t in abcTokenizer(self.innerStr) if
                  isinstance(t, (ABCArticulation, ABCExpression, ABCNote))]

        self._first_note = next((t for t in tokens if isinstance(t, ABCNote)), None)

    def quarterLength(self, defaultQuarterLength: Optional[float]=None):
        """
        Get the length of this chord relative to a quarter note.

        Arguments:
            defaultQuarterLength:
                Optionally, a different DefaultQuarterLength can be specified.
                The quarter note length of the object is not replaced.
        Returns:
            CHord length relative to quarter note


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
        if self.isEmpty:
            return 0

        if defaultQuarterLength is None:
            defaultQuarterLength = self.activeDefaultQuarterLength

        # The length of a chord is determined by the product of the length modifier,
        # the brokenRyhmus modifier and the length of the first note of the chord.
        return self.lengthModifier * self.brokenRyhtmModifier * self._first_note.quarterLength(defaultQuarterLength)

    def m21Object(self) -> chord.Chord:
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

        notes = []
        for n in self.subTokens:
            if isinstance(n, ABCNote) and not n.isRest:
                n.activeKeySignature = self.activeKeySignature
                n.activeDefaultQuarterLength = self.activeDefaultQuarterLength
                notes.append(n.m21Object())

        c = chord.Chord(notes)
        c.duration.quarterLength = self.quarterLength()
        self.apply_articulations(c)
        self.apply_expressions(c)
        self.apply_spanners(c)
        self.apply_tuplet(c)

        if self.inGrace:
            c = c.getGrace()

        return c


TOKEN_SPEC = {}
for token_base_class in [ABCMark, ABCAccent, ABCArticulation, ABCExpression]:
    for token_class in token_base_class.__subclasses__():
        if token_class is not token_base_class:
            if not hasattr(token_class, 'REGEX'):
                environLocal.printDebug(
                    [f'Token class "{token_class.__name__}" has no attribut "REGEX"'])
            elif not token_class.REGEX:
                environLocal.printDebug(
                    [f'Attribut "REGEX" of "{token_class.__name__}" is not defined'])
            else:
                TOKEN_SPEC[f'{token_class.__name__}'] = (f"{token_class.REGEX}", token_class)

TOKEN_RE = re.compile(r'|'.join(r'(?P<%s>%s)' % (rule, v[0])
                                for rule, v in TOKEN_SPEC.items()),
                           re.MULTILINE)


TOKEN_SPEC = {
    'COMMENT': ('[ ]*%.*$', None),
}

def register_token_class(token_class: Type, recursive: bool = True):
    if token_class not in [ABCToken, ABCMark, ABCArticulation, ABCExpression,
                           ABCSpanner, ABCParenStop, ABCGeneralNote]:
        if issubclass(token_class, ABCSpanner):
            if not hasattr(token_class, 'REGEX_START'):
                environLocal.printDebug(
                    [f'Token class "{token_class.__name__}" has no attribut "REGEX_START"'])
            elif not token_class.REGEX_START:
                environLocal.printDebug(
                    [f'Attribut "REGEX_START" of ABCSpanner "{token_class.__name__}" is not defined'])
            elif not hasattr(token_class, 'REGEX_STOP'):
                environLocal.printDebug(
                    [f'Token class "{token_class.__name__}" has no attribut "REGEX_START"'])
            elif not token_class.REGEX_STOP:
                environLocal.printDebug(
                    [f'Attribut "REGEX_START" of ABCSpanner "{token_class.__name__}" is not defined'])
            else:
                TOKEN_SPEC[f'{token_class.__name__}'] = (f"{token_class.REGEX_START}", token_class)
                TOKEN_SPEC[f'{token_class.__name__}_STOP'] = (f"{token_class.REGEX_STOP}", ABCParenStop)

        elif not hasattr(token_class, 'REGEX'):
            environLocal.printDebug(
                [f'Token class "{token_class.__name__}" has no attribut "REGEX"'])
        elif not token_class.REGEX:
            environLocal.printDebug(
                [f'Attribut "REGEX" of "{token_class.__name__}" is not defined'])
        else:
            TOKEN_SPEC[f'{token_class.__name__}'] = (f"{token_class.REGEX}", token_class)

    if recursive:
        for sub_class in token_class.__subclasses__():
            if sub_class != token_class:
                register_token_class(token_class=sub_class, recursive=recursive)


register_token_class(token_class=ABCToken)

# Build a regular expression for the tokenizer from TOKEN_SPEC
TOKEN_RE = re.compile(r'|'.join(r'(?P<%s>%s)' % (rule, v[0])
                                for rule, v in TOKEN_SPEC.items()),
                           re.MULTILINE)

def abcTokenizer(src: str, abcVersion=None) -> List[ABCToken]:
    """
    Tokenizer for ABC formated strings

    Arguments:
        src: abc string
        abcVersion: Version of the ABC Format
    """
    tokens: List[ABCToken] = []

    for m in TOKEN_RE.finditer(src):
        rule = m.lastgroup
        value = m.group()

        # Some barlines are replaced by multiple tokens
        if rule == 'BARLINE':
            tokens.extend(
                ABCBar.barlineTokenFilter(value)
            )
            continue

        # Tokenize the internal abc string of a Chord
        if rule == 'CHORD':
            token = ABCChord(value, parent_handler=self)
            token.tokenize()
            tokens.append(token)
            continue

        # Lookup an ABCToken class for the rule and create the token
        regex, token_class = TOKEN_SPEC[rule]
        if token_class:
            try:
                tokens.append(token_class(src=value))
            except Exception as e:
                raise ABCHandlerException(f'Creating token [{token_class}] failed.\n{e}')
        else:
            environLocal.printDebug(
                [f'No token class for rule "{rule}" with matching regex "{regex}"'])

    return tokens

# ------------------------------------------------------------------------------
class Test(unittest.TestCase):
    pass

# ------------------------------------------------------------------------------
# define presented order in documentation
# _DOC_ORDER = [ABCFile, ABCHandler, ABCHandlerBar]

if __name__ == '__main__':
    # sys.arg test options will be used in mainTest()
    import music21

    music21.mainTest(Test)

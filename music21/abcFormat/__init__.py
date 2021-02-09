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
    'ABCHandler', 'ABCHandlerBar',
    'mergeLeadingMetaData',
    'ABCFile',
]

import copy
import io
import pathlib
import re
import unittest
from typing import Union, Optional, List, Tuple, Type, Dict, Callable
import itertools

from music21 import common
from music21 import environment
from music21 import exceptions21
from music21 import prebase
from music21 import articulations
from music21 import expressions
from music21 import dynamics
from music21 import repeat

from music21.abcFormat import translate
from music21.abcFormat import testFiles

environLocal = environment.Environment('abcFormat')

# for implementation
# see http://abcnotation.com/abc2mtex/abc.txt

# Map ABC Decorations to music21 articulation & expression classes

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

# Specification & regular expression of a voice metadata field

# store a mapping of ABC representation to pitch values
_pitchTranslationCache = {}

# ------------------------------------------------------------------------------
# note inclusion of w: for lyrics
RE_ABC_NOTE = re.compile(r'([\^_=]*)([A-Ga-gz])([0-9/\',]*)')
RE_ABC_VERSION = re.compile(r'(?:((^[^%].*)?[\n])*%abc-)(\d+)\.(\d+)\.?(\d+)?')

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

    @staticmethod
    def stripComment(strSrc):
        '''
        removes ABC-style comments from a string:

        >>> ao = abcFormat.ABCToken()
        >>> ao.stripComment('asdf')
        'asdf'
        >>> ao.stripComment('asdf%234')
        'asdf'
        >>> ao.stripComment('asdf  %     234')
        'asdf  '
        >>> ao.stripComment('[ceg]% this chord appears 50% more often than other chords do')
        '[ceg]'

        This is a static method, so it can also be called on the class itself:

        >>> abcFormat.ABCToken.stripComment('b1 % a b-flat actually')
        'b1 '

        Changed: v6.2 -- made a staticmethod
        '''
        if '%' in strSrc:
            return strSrc.split('%')[0]
        return strSrc

    def m21Object(self):
        return None

class ABCMark(ABCToken):
    '''
    Base class of abc score marker token
    Marker can placed on every position in a stream.
    '''

    def __init__(self, src: str, m21class: Optional[Type] = None):
        super().__init__(src)
        self.m21Class = m21class

    def m21Object(self):
        return self.m21Class()


class ABCDynamic(ABCMark):
    def m21Object(self) -> dynamics.Dynamic:
        """
        >>> abcFormat.ABCDynamic('!ppp!').m21Object()
        <music21.dynamics.Dynamic ppp>
        """
        return dynamics.Dynamic(self.src[1:-1])


class ABCSpanner(ABCToken):
    """
    Defines a base class for all spanner type tokens
    """

    def m21Object(self):
        return self._spannerObj


class ABCArticulation(ABCToken):
    def __init__(self: str, src, m21Class: Type):
        super().__init__(src)
        self.m21Class = m21Class

    def m21Object(self):
        return self.m21Class()


class ABCExpression(ABCToken):
    def __init__(self: str, src, m21Class: Type):
        super().__init__(src)
        self.m21Class = m21Class

    def m21Object(self):
        return self.m21Class()


class ABCAnnotations(ABCMark):
    """
    ABC text Annotations are set in quotation marks, the first charakter indicate where the
    annotation has set relative to the next note
    ^ : above the noe
    _ : below the note
    < : left of the note
    > : right of the note
    @ : indicate a free placement position
    """
    TOKEN_REGEX = '"[\^_<>@][^"]*"'

    def __init__(self, src: str):
        super().__init__(src.strip('"'), expressions.TextExpression)

    def m21Object(self):
        te = expressions.TextExpression(self.src[1:])
        # musicscore is ignoring relative-x / relative-y (need an other tool to check the placement directives)
        if self.src[0] == '^':
            te.positionPlacement = 'above'
        elif self.src[0] == '_':
            te.positionPlacement = 'below'
        return te


class ABCCrescStart(ABCSpanner):
    '''
    ABCCrescStart tokens always precede the notes in a crescendo.
    These tokens coincide with the string "!crescendo(";
    the closing string "!crescendo)" counts as an ABCParenStop.
    '''

    def __init__(self, src):
        super().__init__(src)
        from music21 import dynamics
        self._spannerObj = dynamics.Crescendo()


class ABCDimStart(ABCSpanner):
    '''
    ABCDimStart tokens always precede the notes in a diminuendo.
    They function identically to ABCCrescStart tokens.
    '''

    def __init__(self, src):  # previous typo?: used to be __init
        super().__init__(src)
        from music21 import dynamics
        self._spannerObj = dynamics.Diminuendo()


class ABCFingering(ABCArticulation):
    def __init__(self, src: str):
        super().__init__(src, articulations.Fingering)

    def m21Object(self):
        return self.m21Class(self.src)


class ABCParenStop(ABCToken):
    TOKEN_REGEX = r'\)'


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
    'fermata': expressions.Fermata,
    'trill': expressions.Trill,
    'coda': repeat.Coda,
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


class ABCDecoration(ABCToken):
    """
    ABCDecoration is a factory class for ABCArticulation & ABCExpression
    """
    TOKEN_REGEX = '!.*?!|[\.]'

    def __new__(cls, src: str):
        """
        Instead of an ABCDecoration object, an ABCExpression, ABCArticulation
        or ABCMark is returned. It use the mapping M21_DECORATIONS and implements
        dynamics and fingering as special cases.

        >>> abcFormat.ABCDecoration("!trill!")
        <music21.abcFormat.ABCExpression '!trill!'>
        >>> abcFormat.ABCDecoration("!tenuto!")
        <music21.abcFormat.ABCArticulation '!tenuto!'>
        >>> abcFormat.ABCDecoration("!ppp!")
        <music21.abcFormat.ABCDynamic '!ppp!'>
        """
        map_key = src.strip('!').strip('+')
        try:

            decoration_class = M21_DECORATIONS[map_key]
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
            if map_key in "12345":
                return ABCFingering(map_key)
            if map_key in ['p', 'pp', 'ppp', 'pppp', 'f', 'ff', 'fff',
                           'ffff', 'mp', 'mf', 'sfz']:
                return ABCDynamic(src)
            raise ABCTokenException(f'Unknown abc decoration "{src}"')

class ABCMetadata2(ABCToken):
    r'''
    Defines a token of metadata in ABC.

    >>> md = abcFormat.ABCMetadata('I:linebreak')
    >>> md.src
    'I:linebreak'

    Has two attributes, `tag` and `data` which are strings

    >>> md.tag
    'I'
    >>> md.data
    'linebreak'
    '''
    TOKEN_REGEX = r'^[A-Za-vxz]:.*$'

    # given a logical unit, create an object
    # may be a chord, notes, metadata, bars

    def __init__(self, src=''):
        '''
        Called before contextual adjustments and needs
        to have access to data.  Divides a token into
        tag (a single capital letter or w) and .data representations.

        >>> x = abcFormat.ABCMetadata('T:tagData')
        >>> x.tag
        'T'
        >>> x.data
        'tagData'
        '''
        super().__init__(src)
        parts = src.split(':', 1)
        self.data: Optional[str] = None
        self.tag: str = parts[0].strip()
        # remove comments
        self.data = parts[1].split('%', 1)[0].strip()

    def isInstruction(self) -> bool:
        return self.tag == 'I'

    def isUserDefined(self) -> bool:
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
        if self.tag == 'X':
            return True
        return False

    def isMeter(self) -> bool:
        '''
        Returns True if the tag is "M" for meter, False otherwise.
        '''
        if self.tag == 'M':
            return True
        return False

    def isTitle(self) -> bool:
        '''
        Returns True if the tag is "T" for title, False otherwise.
        '''
        if self.tag == 'T':
            return True
        return False

    def isComposer(self) -> bool:
        '''
        Returns True if the tag is "C" for composer, False otherwise.
        '''
        if self.tag == 'C':
            return True
        return False

    def isOrigin(self) -> bool:
        '''
        Returns True if the tag is "O" for origin, False otherwise.
        This value is set in the Metadata `localOfComposition` of field.
        '''
        if self.tag == 'O':
            return True
        return False

    def isVoice(self) -> bool:
        '''
        Returns True if the tag is "V", False otherwise.
        '''
        if self.tag == 'V':
            return True
        return False

    def isKey(self) -> bool:
        '''
        Returns True if the tag is "K", False otherwise.
        Note that in some cases a Key will encode clef information.

        (example from corpus: josquin/laDeplorationDeLaMorteDeJohannesOckeghem.abc)
        '''
        if self.tag == 'K':
            return True
        return False

    def isTempo(self) -> bool:
        '''
        Returns True if the tag is "Q" for tempo, False otherwise.
        '''
        if self.tag == 'Q':
            return True
        return False

    def getUserDefined(self) -> Tuple[str, Optional[List[ABCToken]]]:
        symbol, definition = self.data.split('=')
        definition = definition.strip()
        if definition in ['!nil!', '!none!']:
            tokens = None
        else:
            tokens = ABCHandler().tokenize(definition)
        return symbol.strip(), tokens

    def getTimeSignatureParameters(self):
        '''
        If there is a time signature representation available,
        get a numerator, denominator and an abbreviation symbol.
        To get a music21 :class:`~music21.meter.TimeSignature` object, use
        the :meth:`~music21.abcFormat.ABCMetadata.getTimeSignatureObject` method.

        >>> am = abcFormat.ABCMetadata('M:2/2')
        >>> am.isMeter()
        True
        >>> am.getTimeSignatureParameters()
        (2, 2, 'normal')

        >>> am = abcFormat.ABCMetadata('M:C|')
        >>> am.getTimeSignatureParameters()
        (2, 2, 'cut')

        >>> am = abcFormat.ABCMetadata('M: none')
        >>> am.getTimeSignatureParameters() is None
        True

        >>> am = abcFormat.ABCMetadata('M: FREI4/4')
        >>> am.getTimeSignatureParameters()
        (4, 4, 'normal')
        '''
        if not self.isMeter():
            raise ABCTokenException('no time signature associated with this metadata')

        if self.data.lower() == 'none':
            return None
        elif self.data == 'C':
            n, d = 4, 4
            symbol = 'common'  # m21 compat
        elif self.data == 'C|':
            n, d = 2, 2
            symbol = 'cut'  # m21 compat
        else:
            n, d = self.data.split('/')
            # using get number from string to handle odd cases such as
            # FREI4/4
            n = int(common.getNumFromStr(n.strip())[0])
            d = int(common.getNumFromStr(d.strip())[0])
            symbol = 'normal'  # m21 compat
        return n, d, symbol

    def getTimeSignatureObject(self):
        '''
        Return a music21 :class:`~music21.meter.TimeSignature`
        object for this metadata tag, if isMeter is True, otherwise raise exception.

        >>> am = abcFormat.ABCMetadata('M:2/2')
        >>> ts = am.getTimeSignatureObject()
        >>> ts
        <music21.meter.TimeSignature 2/2>

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
            numerator, denominator, unused_symbol = parameters
            return meter.TimeSignature(f'{numerator}/{denominator}')

    def getKeySignatureParameters(self):
        # noinspection SpellCheckingInspection
        '''
        Extract key signature parameters, include indications for mode,
        and translate sharps count compatible with m21,
        returning the number of sharps and the mode.

        >>> from music21 import abcFormat

        >>> am = abcFormat.ABCMetadata('K:Eb Lydian')
        >>> am.getKeySignatureParameters()
        (-2, 'lydian')

        >>> am = abcFormat.ABCMetadata('K:APhry')
        >>> am.getKeySignatureParameters()
        (-1, 'phrygian')

        >>> am = abcFormat.ABCMetadata('K:G Mixolydian')
        >>> am.getKeySignatureParameters()
        (0, 'mixolydian')

        >>> am = abcFormat.ABCMetadata('K: Edor')
        >>> am.getKeySignatureParameters()
        (2, 'dorian')

        >>> am = abcFormat.ABCMetadata('K: F')
        >>> am.getKeySignatureParameters()
        (-1, 'major')

        >>> am = abcFormat.ABCMetadata('K:G')
        >>> am.getKeySignatureParameters()
        (1, 'major')

        >>> am = abcFormat.ABCMetadata('K:Gm')
        >>> am.getKeySignatureParameters()
        (-2, 'minor')

        >>> am = abcFormat.ABCMetadata('K:Hp')
        >>> am.getKeySignatureParameters()
        (2, None)

        >>> am = abcFormat.ABCMetadata('K:G ionian')
        >>> am.getKeySignatureParameters()
        (1, 'ionian')

        >>> am = abcFormat.ABCMetadata('K:G aeol')
        >>> am.getKeySignatureParameters()
        (-2, 'aeolian')

        '''
        # placing this import in method for now; key.py may import this module
        from music21 import key

        if not self.isKey():
            raise ABCTokenException('no key signature associated with this metadata.')

        # abc uses b for flat in key spec only
        keyNameMatch = ['c', 'g', 'd', 'a', 'e', 'b', 'f#', 'g#', 'a#',
                        'f', 'bb', 'eb', 'd#', 'ab', 'e#', 'db', 'c#', 'gb', 'cb',
                        # HP or Hp are used for highland pipes
                        'hp']

        # if no match, provide defaults,
        # this is probably an error or badly formatted
        standardKeyStr = 'C'
        stringRemain = ''
        # first, get standard key indication
        for target in sorted(keyNameMatch, key=len, reverse=True):
            if target == self.data[:len(target)].lower():
                # keep case
                standardKeyStr = self.data[:len(target)]
                stringRemain = self.data[len(target):]
                break

        if len(standardKeyStr) > 1 and standardKeyStr[1] == 'b':
            standardKeyStr = standardKeyStr[0] + '-'

        mode = None
        stringRemain = stringRemain.strip()
        if stringRemain == '':
            # Assume mode is major by default
            mode = 'major'
        else:
            # only first three characters are parsed
            modeCandidate = stringRemain.lower()
            for match, modeStr in (
                ('dor', 'dorian'),
                ('phr', 'phrygian'),
                ('lyd', 'lydian'),
                ('mix', 'mixolydian'),
                ('maj', 'major'),
                ('ion', 'ionian'),
                ('aeo', 'aeolian'),
                ('m', 'minor'),
            ):
                if modeCandidate.startswith(match):
                    mode = modeStr
                    break

        # Special case for highland pipes
        # replace a flat symbol if found; only the second char
        if standardKeyStr == 'HP':
            standardKeyStr = 'C'  # no sharp or flats
            mode = None
        elif standardKeyStr == 'Hp':
            standardKeyStr = 'D'  # use F#, C#, Gn
            mode = None

        # not yet implemented: checking for additional chromatic alternations
        # e.g.: K:D =c would write the key signature as two sharps
        # (key of D) but then mark every  c  as  natural
        return key.pitchToSharps(standardKeyStr, mode), mode

    def getKeySignatureObject(self):
        # noinspection SpellCheckingInspection,PyShadowingNames
        '''
        Return a music21 :class:`~music21.key.KeySignature` or :class:`~music21.key.Key`
        object for this metadata tag.


        >>> am = abcFormat.ABCMetadata('K:G')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.Key of G major>

        >>> am = abcFormat.ABCMetadata('K:Gmin')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.Key of g minor>
        >>> ks.sharps
        -2

        Note that capitalization does not matter
        (http://abcnotation.com/wiki/abc:standard:v2.1#kkey)
        so this should still be minor.

        >>> am = abcFormat.ABCMetadata('K:GM')
        >>> ks = am.getKeySignatureObject()
        >>> ks
        <music21.key.Key of g minor>
        '''
        if not self.isKey():
            raise ABCTokenException('no key signature associated with this metadata')
        from music21 import key
        # return values of getKeySignatureParameters are sharps, mode
        # need to unpack list w/ *
        sharps, mode = self.getKeySignatureParameters()
        ks = key.KeySignature(sharps)
        if mode in (None, ''):
            return ks
        else:
            return ks.asKey(mode)

    def getClefObject(self) -> Tuple[Optional['music21.clef.Clef'], Optional[int]]:
        '''
        Extract any clef parameters stored in the key metadata token.
        Assume that a clef definition suggests a transposition.
        Return both the Clef and the transposition.

        Returns a two-element tuple of clefObj and transposition in semitones

        >>> am = abcFormat.ABCMetadata('K:Eb Lydian bass')
        >>> am.getClefObject()
        (<music21.clef.BassClef>, -24)
        '''
        if self.isKey():

            # placing this import in method for now; key.py may import this module
            clefObj = None
            t = None

            from music21 import clef
            if '-8va' in self.data.lower():
                clefObj = clef.Treble8vbClef()
                t = -12
            elif 'bass' in self.data.lower():
                clefObj = clef.BassClef()
                t = -24

            # if not defined, returns None, None
            return clefObj, t

        elif self.isVoice():
            voicedata = self.getVoiceData()

            # placing this import in method for now; key.py may import this module
            clefObj = None
            t = None

            from music21 import clef

            try:
                clefstr = voicedata['clef'].lower()
                if 'treble-8' == clefstr:
                    clefObj = clef.Treble8vbClef()
                    t = -12
                elif 'bass' == clefstr:
                    clefObj = clef.BassClef()
                    t = -24
            except KeyError:
                pass
            # if not defined, returns None, None
            return clefObj, t

        else:
            raise ABCTokenException(
                'no key signature associated with this metadata; needed for getting Clef Object')

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
        mmObj.style.fontStyle = 'italic'
        return mmObj

    def getDefaultQuarterLength(self) -> float:
        r'''
        If there is a quarter length representation available, return it as a floating point value

        Meter is only used for default length if there is no L:

        >>> x = 'X:1\nL:1/4\nM:3/4\n\nf'
        >>> sc = converter.parse(x, format='abc')
        >>> sc.flat.notes[0].duration.type
        'quarter'

        >>> am = abcFormat.ABCMetadata('L:1/2')
        >>> am.getDefaultQuarterLength()
        2.0

        >>> am = abcFormat.ABCMetadata('L:1/8')
        >>> am.getDefaultQuarterLength()
        0.5

        >>> am = abcFormat.ABCMetadata('M:C|')
        >>> am.getDefaultQuarterLength()
        0.5


        If taking from meter, find the "fraction" and if < 0.75 use sixteenth notes.
        If >= 0.75 use eighth notes.

        >>> am = abcFormat.ABCMetadata('M:2/4')
        >>> am.getDefaultQuarterLength()
        0.25

        >>> am = abcFormat.ABCMetadata('M:3/4')
        >>> am.getDefaultQuarterLength()
        0.5


        >>> am = abcFormat.ABCMetadata('M:6/8')
        >>> am.getDefaultQuarterLength()
        0.5


        Meter is only used for default length if there is no L:

        >>> x = 'L:1/4\nM:3/4\n\nf'
        >>> sc = converter.parse(x, format='abc')
        >>> sc.flat.notes[0].duration.type
        'quarter'
        '''
        # environLocal.printDebug(['getDefaultQuarterLength', self.data])
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
            n, d, unused_symbol = parameters
            if n / d < 0.75:
                return 0.25  # less than 0.75 the default is a sixteenth note
            else:
                return 0.5  # otherwise it is an eighth note
        else:  # pragma: no cover
            raise ABCTokenException(
                f'no quarter length associated with this metadata: {self.data}')

    def getInstruction(self) -> Optional[Tuple[str, str]]:
        """ Get an instruction field.
        >>> abcFormat.ABCMetadata('I:midi instrument=16').getInstruction()
        ('MIDI', 'instrument=16')
        """
        if not self.isInstruction():
            raise ABCTokenException('no instruction associated with this metadata')

        parts = self.data.split(' ', 1)
        key = parts[0].strip().upper()
        try:
            data = parts[1].strip()
            return key, data
        except IndexError:
            return key, ''


class ABCInlineMetadata(ABCMetadata):
    TOKEN_REGEX = r'[\[][IKLMmNPQRrUV][:][^\]]*[\]]'

    def __init__(self, src: str):
        super().__init__(src[1:-1])

from music21 import clef

CLEF_RE =  r'(?P<name>clef\s*=\s*\S+(?!\S))'
CLEF_RE += r'|(?P<octave>octave=.*(?!\S))'
CLEF_RE += r'|(?P<transpose>t(ranspose)?\s*=\s*[+\-]?[0-9]+(?!\S))'
CLEF_RE += r'|(?P<unamed>[^=]+?(?!\S))'
CLEF_RE = re.compile(CLEF_RE, re.MULTILINE)

class ABCClefMixin():
    """
    The clefs with the +/-8 at then end transpose the melody an octave
    up or down if no octave modifier has explicitly set.
    When specifying the name, the 'clef=' can also be omitted

    >>> md = abcFormat.ABCClefMixin('clef=treble')
    >>> md.name
    'treble'

    >>> md = abcFormat.ABCClefMixin('bass-8')
    >>> md.clef
    <music21.clef.Bass8vbClef>
    >>> md.octave
    -1

    >>> md = abcFormat.ABCClefMixin('clef="treble+8"')
    >>> md.clef
    <music21.clef.Treble8vaClef>
    >>> md.octave
    1

    >>> md = abcFormat.ABCClefMixin('clef=treble+8 octave=-2')
    >>> md.clef
    <music21.clef.Treble8vaClef>
    >>> md.octave
    -2
    """
    CLEF_NAMES = {
        'G1': clef.FrenchViolinClef,
        'treble': clef.TrebleClef, 'g2': clef.TrebleClef,
        'treble-8': clef.Treble8vbClef, 'treble+8': clef.Treble8vaClef,
        'bass3': clef.CBaritoneClef, 'baritone': clef.CBaritoneClef,
        'f3': clef.CBaritoneClef, 'bass': clef.BassClef,
        'f4': clef.BassClef, 'bass-8': clef.Bass8vbClef,
        'bass+8': clef.Bass8vaClef, 'f5': clef.SubBassClef,
        'tenor': clef.TenorClef, 'c4': clef.TenorClef,
        'alto': clef.AltoClef, 'c3': clef.AltoClef,
        'alto1': clef.SopranoClef, 'soprano': clef.SopranoClef,
        'c1': clef.SopranoClef, 'alto2': clef.MezzoSopranoClef,
        'mezzosoprano': clef.MezzoSopranoClef, 'c2': clef.MezzoSopranoClef
    }

    def __init__(self, data: str):
        self.transpose = 0
        self.name = None
        self.octave = None
        self.clef = None

        # list of unamed matches
        unamed  = []
        for m in CLEF_RE.finditer(data):
            k = m.lastgroup
            v = m.group()
            if k in ['transpose', 'octave', 'name']:
                setattr(self, k, v.split('=')[1].strip().strip('"'))
            else:
                unamed.append(v.strip())

        # the clef name is allowed without clef=<name>
        if self.name is None:
            for tag in unamed:
                if tag in ABCClefMixin.CLEF_NAMES:
                    self.name = tag
                    break

        if self.name:
            self.clef = ABCClefMixin.CLEF_NAMES[self.name]()

        if self.octave is None:
            self.octave = self.clef.octaveChange if self.clef else 0
        else:
            self.octave = int(self.octave)

        # currenty unused
        self.transpose = int(self.transpose)


class ABCMetadata(ABCToken):
    def __int__(self, src):
        super().__init__(src)
        if src.startswith('['):
            self.inlined = True
            src = src[1:-1]
        else:
            self.inlined = False

        parts = src.split(':', 1)
        self.tag: str = parts[0].strip()
        # remove comments
        self.data: str = parts[1].split('%', 1)[0].strip()


VOICE_RE = re.compile(r'(?P<id>^\S+)|(?P<name>(name|nm)\s*=\s*\S+)|(?P<subname>(subname|snm)\s*=\s*\S+)')
class ABCVoice(ABCMetadata, ABCClefMixin):
    def __init__(self, data):
        r"""
        >>> v = abcFormat.ABCVoice('V:1 nm="piano" subname=accompaniment')
        >>> v.id
        '1'
        >>> v.name
        'piano'
        >>> v.subname
        'accompaniment'

        We got also clef informations from a voice field
        >>> v = abcFormat.ABCVoice("V:1 treble")
        >>> v.clef
        'treble'
        """
        super().__init__(src)
        ABCClefMixin.__init__(self, self.data)
        self.id : str = ''
        self.name : Optional[str] = None
        self.subname: Optional[str] = None
        for m in VOICE_RE.finditer(self.data):
            k = m.lastgroup
            v = m.group()
            if k == 'id':
                self.id = v
            elif k == 'name':
                self.name = v.split('=')[1].strip().strip('"')
            elif k == 'subname':
                self.subname = v.split('=')[1].strip().strip('"')


class ABCKey(ABCMetadata, ABCClefMixin):
    def __init__(self, src: str):
        r"""
        This is the Token for the ABC filed 'K:'
        The ABCKey specified a Key or KeySignature.
        However, it can also specify a clef in addition.
        The clef follows after the key and key modifications

        >>> k = abcFormat.ABCVoice("K:Cm")
        >>> k.getKeySignatureObject()

        We got also clef informations from a key field
        >>> v = abcFormat.ABCVoice("K:C treble")
        >>> v.clef
        'treble'
        """

        super().__init__(src)
        ABCClefMixin.__init__(self, self.data)

        self.tonic: str = None
        self.mode: str = None
        self.accidental_modifications: List[str]


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

        # [clef=]<clef name>[<line number>][+8 | -8] [middle=<pitch>] [transpose=<semitones>] [octave=<number>] [stafflines=<lines>]"

RE_ABC_LYRICS = re.compile(r'[*\-_|]|[^*\-|_ ]+[\-]?')


class ABCLyrics(ABCToken):

    TOKEN_REGEX = r'w:.*((([\][\n]w)|([\n][+])):.*)*'
    def __init__(self, src: str):
        r'''
        >>> abc = ('w: ||A- ve Ma- ri- -\\\nw: |a! Jung- - - frau *|')
        >>> ah = abcFormat.ABCHandler()
        >>> w = ah.tokenize(abc)
        >>> w[0].syllables
        ['|', '|', 'A-', 've', 'Ma-', 'ri-', '-', '|', 'a!', 'Jung-', '-', '-', 'frau', '*', '|']

        >>> abc = ('w: |\n+:|A- ve Ma- ri- -\\\nw: |a! Jung- - - frau *|')
        >>> ah = abcFormat.ABCHandler()
        >>> w = ah.tokenize(abc)
        >>> w[0].syllables
        ['|', '|', 'A-', 've', 'Ma-', 'ri-', '-', '|', 'a!', 'Jung-', '-', '-', 'frau', '*', '|']

        >>> abc = ('w: ||A- ve Ma- ri- -|a! Jung- - - frau *|')
        >>> ah = abcFormat.ABCHandler()
        >>> w = ah.tokenize(abc)
        >>> w[0].syllables
        ['|', '|', 'A-', 've', 'Ma-', 'ri-', '-', '|', 'a!', 'Jung-', '-', '-', 'frau', '*', '|']

        '''

        src = " ".join(s[2:].strip(r'\\') for s in src.split('\n'))
        super().__init__(src)

        self.syllables = [s for s in RE_ABC_LYRICS.findall(self.src)]
        #breakpoint()

class ABCDirective(ABCToken):
    """
    The ABC Directive is a factory for an ABCMetadata object
    with the tag for an instruction (I:)
    """
    TOKEN_REGEX = '%%.*'

    def __new__(cls, src: str):
        return ABCMetadata(f'I:{src}')


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
        super().__init__(src)
        self.slurObj = None
        from music21 import spanner
        self._spannerObj = spanner.Slur()


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
    TOKEN_REGEX = r'"[^\^<>_@][^"]*"'

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

    @classmethod
    def _parse_abc_length(self, src: str) -> float:
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

    def m21Object(self):
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
    TOKEN_REGEX = r'[zZ][0-9/]*'

    def __init__(self, src):
        super().__init__(src, length=src[1:])

    def m21Object(self):
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

    def getPitchName(self,
                     keySignature: Optional['music21.key.KeySignature'] = None,
                     carriedAccidental: Optional[str] = None) -> Tuple[str, bool]:
        """
        Parse the note & return
        """

        if keySignature is None:
            keySignature = self.keySignature

        if carriedAccidental is None:
            carriedAccidental = self.carriedAccidental

        cache_key = (self.pitchClass,
                     self.octave,
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

            result = (f'{self.pitchClass}{accidental}{self.octave}', display)
            _pitchTranslationCache[cache_key] = result
            return result

    def m21Object(self) -> Union['music21.note.Note', 'music21.note.Rest']:
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
        pitchName, accidentalDisplayStatus = self.getPitchName()
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
        return n


class ABCChord(ABCGeneralNote):
    '''
    A representation of an ABC Chord, which contains within its delimiters individual notes.

    A subclass of ABCNote.
    '''

    # Regular expression matching an ABCCHord
    TOKEN_REGEX = r'[\[][^\]:]*[\]][0-9]*[/]*[0-9]*'

    def __init__(self, src: str, parent: Optional['ABCHandler'] = None):
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
        self.parent: ABCHandler = parent

        # Carried acidentials is for the inner tokens
        self.carriedAccidentals: Optional[Dict[str, str]] = None

        # The chord has is own handler for the internal Notes
        abc_version = None if parent is None else parent.abcVersion
        self.chordHandler = ABCHandler(abcVersion=abc_version)

        # tokenize the inner string of the chord.
        # Only articulations, expressions and notes are relevant
        tokens = [t for t in self.chordHandler.tokenize(self.innerStr) if
                  isinstance(t, (ABCArticulation, ABCExpression, ABCNote))]

        self.chordHandler.tokens = tokens
        self._first_note: Optional[ABCNote] = next((t for t in tokens
                                                    if isinstance(t, ABCNote)), None)

    @property
    def tokens(self):
        return self.chordHandler.tokens

    def innerTokensProcess(self):

        # Set context for the chord handler
        ch = self.chordHandler
        ch.carriedAccidentals = self.parent.carriedAccidentals
        ch.lastKeySignature = self.parent.lastKeySignature
        ch.lastDefaultQL = self.parent.lastDefaultQL
        # process the inner chord tokens
        ch.tokenProcess()

    @property
    def subTokens(self) -> List[Union[ABCNote, ABCExpression, ABCArticulation]]:
        '''
        Internal note tokens and there expressions & articulations
        '''
        return self.chordHandler.tokens

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

    def m21Object(self) -> 'music21.chord.Chort':
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

        notes = [n.m21Object() for n in self.subTokens if isinstance(n, ABCNote)]

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


TOKEN_SPEC: Dict[str, Tuple[str, Optional[Callable]]] = {'COMMENT': ('%(?=[^%]).*$', None),
                                                         'LINE_CONTINUE': (r'\\n', None)}


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


# ------------------------------------------------------------------------------
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

    def __init__(self, tokens: Optional[List[ABCToken]] = None, abcVersion: Optional[ABCVersion] = None,
                 lineBreaksDefinePhrases: bool = False):

        # tokens are ABC objects import n a linear stream
        self._abcVersion: Optional[ABCVersion] = abcVersion
        self.abcDirectives: Dict[str, str] = {}
        self.userDefined: Dict[str, List[ABCToken]] = {}
        self.tokens = [] if tokens is None else tokens
        self.activeSpanner = []
        self.lastKeySignature = None
        self.lastDefaultQL: Optional[float] = None
        self.carriedAccidentals: Dict[str, str] = {}
        self.lineBreaksDefinePhrases: bool = lineBreaksDefinePhrases
        self.strSrc: str = ''
        self.srcLen = len(self.strSrc)  # just documenting this.
        self.lastTimeSignatureObj = None  # an m21 object
        self.lastTupletToken = None  # a token obj; keeps count of usage
        self.lastTieToken = None
        self.lastGraceToken = None
        self.lastNoteToken = None
        self.lastArticulations = []
        self.lastExpressions = []
        self.lastBrokenRhythm = None
        self.accidental_propagation = self._accidentalPropagation()
        self.lyrics = None
        # On this Note, starts the last known lyric line(s)
        self.lastLyricNote: ABCGeneralNote = None

    @property
    def abcVersion(self):
        return self._abcVersion

    @abcVersion.setter
    def abcVersion(self, abcVersion: ABCVersion):
        self._abcVersion = abcVersion
        # accidential propagation depends on the abcVersion
        self.accidental_propagation = self._accidentalPropagation()

    @classmethod
    def parseABCVersion(cls, src: str) -> Optional[ABCVersion]:
        '''
        Every abc file conforming to the standard should start with the line
        %abc-2.1

        >>> abcFormat.ABCHandler.parseABCVersion('%abc-2.3.2')
        (2, 3, 2)

        Catch only abc version as first comment line
        >>> abcFormat.ABCHandler.parseABCVersion('%first comment\\n%abc-2.3.2')

        But ignore post comments
        >>> abcFormat.ABCHandler.parseABCVersion('X:1 % reference number\\n%abc-2.3.2')
        (2, 3, 2)
        '''
        verMats = RE_ABC_VERSION.match(src)
        if verMats:
            abcMajor = int(verMats.group(3))
            abcMinor = int(verMats.group(4))
            abcPatch = int(verMats.group(5)) if verMats.group(5) else 0
            return (abcMajor, abcMinor, abcPatch)

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

        if 'PROPAGATE-ACCIDENTALS' in self.abcDirectives:
            return self.abcDirectives['PROPAGATE-ACCIDENTALS']
        return 'pitch'  # Default per abc 2.1 standard

    @staticmethod
    def startsMetadata(c: str, cNext: Optional[str], cNextNext: Optional[str]) -> bool:
        '''
        Returns True if this context describes the start of a metadata section, like

        A:something

        Metadata: capital letter, with next char as ':' and some following character

        >>> ah = abcFormat.ABCHandler
        >>> ah.startsMetadata('A', ':', 's')
        True

        lowercase w: is a special case for lyric defs

        >>> ah.startsMetadata('w', ':', 's')
        True

        Following char must be ":"

        >>> ah.startsMetadata('A', ' ', 's')
        False

        Pipe after colon indicates not metadata (bar info).
        For example need to not misinterpret repeat bars as metadata
        e.g. `dAG FED:|2 dAG FGA|`

        this is incorrect, but we can avoid it by
        looking for a leading pipe and returning False

        >>> ah.startsMetadata('A', ':', '|')
        False

        >>> ah.startsMetadata('A', ':', None)
        False
        '''
        if cNext != ':':
            return False
        elif cNextNext is None:
            return False
        elif cNextNext == '|':
            return False
        elif c == 'w':
            return True  # special case, w:...
        elif c.isalpha() and c.isupper():
            return True
        return False

    # --------------------------------------------------------------------------
    # token processing

    def tokenize(self, strSrc: str) -> List[ABCToken]:
        '''
        Walk the abc string, creating ABC objects along the way.

        This may be called separately from process(), in the case
        that pre/post parse processing is not needed.

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('X: 1')
        [<music21.abcFormat.ABCMetadata 'X: 1'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6f')
        [<music21.abcFormat.ABCTuplet '(6'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6:4f')
        [<music21.abcFormat.ABCTuplet '(6:4'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6:4:2f')
        [<music21.abcFormat.ABCTuplet '(6:4:2'>, <music21.abcFormat.ABCNote 'f'>]

        >>> abch = abcFormat.ABCHandler()
        >>> abch.tokenize('(6::2f')
        [<music21.abcFormat.ABCTuplet '(6::2'>, <music21.abcFormat.ABCNote 'f'>]
        '''
        tokens: List[ABCToken] = []
        for m in TOKEN_RE.finditer(strSrc):
            rule = m.lastgroup
            value = m.group()

            if rule == 'ABCDirective':
                # remove the '%%' start of a directive
                value = value[2:]

            # Some barlines are replaced by multiple tokens
            if rule == 'ABCBar':
                tokens.extend(
                    ABCBar.barlineTokenFilter(value)
                )
                continue

            if rule == 'ABCChord':
                tokens.append(ABCChord(src=value, parent=self))
                continue

            # Lookup an ABCToken class for the rule and create the token
            regex, token_class = TOKEN_SPEC[rule]
            if token_class:
                try:
                    tokens.append(token_class(value))
                except ABCTokenException as e:
                    environLocal.printDebug([e])
            else:
                environLocal.printDebug(
                    [f'No token class for rule "{rule}" with matching regex "{regex}"'])

        return tokens

    def process_ABCArticulation(self, token: ABCArticulation):
        """
          Process ABCArticulation tokens
        """
        self.lastArticulations.append(token)

    def process_ABCBar(self, token: ABCBar):
        self.carriedAccidentals = {}

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
        token.innerTokensProcess()
        return token

    def process_ABCExpression(self, token: ABCExpression):
        """
        Process ABCExpression tokens
        """
        self.lastExpressions.append(token)
        return token

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

    def process_ABCMetadata(self, token: ABCMetadata):
        """
        Process ABCMetadata tokens
        """
        if token.isMeter():
            self.lastTimeSignatureObj = token.getTimeSignatureObject()
            # restart matching conditions; match meter twice ok
        if token.isDefaultNoteLength() or (token.isMeter() and self.lastDefaultQL is None):
            self.lastDefaultQL = token.getDefaultQuarterLength()
        elif token.isKey():
            sharpCount, mode = token.getKeySignatureParameters()
            from music21 import key
            self.lastKeySignature = key.KeySignature(sharpCount)
            if mode not in (None, ''):
                self.lastKeySignature = self.lastKeySignature.asKey(mode)
        elif token.isReferenceNumber():
            # reset any spanners at the end of any piece
            self.activeSpanner = []
        elif token.isVoice():
            self.lastLyricNote = None
        elif token.isInstruction():
            instruction = token.getInstruction()
            if instruction is not None:
                k, v = instruction
                self.abcDirectives[k] = v
                if k == 'PROPAGATE-ACCIDENTALS':
                    self.accidental_propagation = self._accidentalPropagation()

        elif token.isUserDefined():
            try:
                symbol, definition = token.getUserDefined()
                if definition is None and symbol in self.userDefined:
                    # return value of None indicates the symbol definition should deleted
                    del self.userDefined[symbol]
                else:
                    self.userDefined[symbol] = definition
            except ABCTokenException as e:
                environLocal.printDebug(['Creating token form UserDefined failed.', e])

    def process_ABCNote(self, token: ABCNote):
        self.process_ABCGeneralNote(token)
        if token.accidental:
            # Remember the active accidentals in the measure
            if self.accidental_propagation == 'octave':
                self.carriedAccidentals[(token.pitchClass, token.octave)] = token.accidental
            elif self.accidental_propagation == 'pitch':
                self.carriedAccidentals[token.pitchClass] = token.accidental
        else:
            if self.accidental_propagation == 'pitch' and token.pitchClass in self.carriedAccidentals:
                token.carriedAccidental = self.carriedAccidentals[token.pitchClass]
            elif self.accidental_propagation == 'octave' and (
                token.pitchClass, token.octave) in self.carriedAccidentals:
                token.carriedAccidental = self.carriedAccidentals[(token.pitchClass, token.octave)]

    def process_ABCParenStop(self, token: ABCParenStop):
        # @TODO: we don't need this, base class process method is called anyway
        if self.activeSpanner:
            self.activeSpanner.pop()

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

    def tokenProcess(self):
        '''
        Process all token objects any supply contextual informations.

        Each token class calls a method named "process_{token.__class.__name__}"
        for processing. If no method with this name has found it will search for a
        method of one of his base classes.
        '''

        tokens = []
        tokenIter = iter(self.tokens)

        while True:
            # get a token from the token iteratur until the StopIteration exception has raised
            try:
                token = next(tokenIter)
                if isinstance(token, ABCSymbol):
                    # this is a special case, we replace the symbol token
                    # with one or more user defined tokens
                    try:
                        # Lookup the symbol in the userDefined dict
                        # Has a default symbol dict as fallback.
                        symbol_tokens = token.lookup(self.userDefined)
                        # chain the symbols in front of tokenIter and continue iterating
                        tokenIter = itertools.chain(symbol_tokens, tokenIter)
                        continue
                    except ABCTokenException as e:
                        # Not defined symbol
                        environLocal.printDebug([e])
                        continue

                # Caching the method lookup has no performance benefits.
                # find a process method for the token by class name
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

                if token_process_method is not None:
                    token_process_method(token)

                tokens.append(token)

            except StopIteration:
                # replace tokens with the collected tokens
                self.tokens = tokens
                break

    def process(self, strSrc: str) -> None:
        if self.abcVersion is None:
            self.abcVersion = self.parseABCVersion(src=strSrc)

        self.strSrc = strSrc
        self.tokens = self.tokenize(strSrc)
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
        for t in self.tokens:
            if isinstance(t, ABCMetadata) and t.isReferenceNumber():
                count += 1
                if count == 2:
                    return True

        return False

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

        for t in self.tokens:
            if isinstance(t, ABCMetadata) and t.isReferenceNumber():
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

    def getReferenceNumber(self):
        r'''
        If tokens are processed, get the first
        reference number defined.


        >>> abcStr = 'X:5\nM:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> junk = ah.process(abcStr)
        >>> ah.getReferenceNumber()
        '5'
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')

        for t in self.tokens:
            if isinstance(t, ABCMetadata) and t.isReferenceNumber():
                return t.data

        return None

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

    def splitByVoice(self) -> List['ABCHandler']:
        r"""
        Split the tokens of this ABCHandler into seperat ABCHandler for each voice.
        Each voice handler got the all the common metadata token of the abc tune header

        The abc header with the common metadata ends with the first 'K:' field or with the
        first non metadata token (bad abc coding).

        ABC directives are treated as metadata instructions.

        We assume there is a voice with the id '1'.  All tokens in the body that
        cannot be assigned to a voice are assigned to the voice '1'. This happens if no
        voices are defined (monophonic tune) or because bad abc coding.

        >>> abcStr = 'M:6/8\nL:1/8\nK:G\nB3 A3 | G6 | B3 A3 | G6 ||'
        >>> ah = abcFormat.ABCHandler()
        >>> ah.process(abcStr)
        >>> tokenColls = ah.splitByVoice()
        >>> tokenColls[0]
        <music21.abcFormat.ABCHandler object at 0x...>
        >>> [t.src for t in tokenColls[0].tokens]
        ['M:6/8', 'L:1/8', 'K:G', 'B3', 'A3', '|', 'G6', '|', 'B3', 'A3', '|', 'G6', '||']

        >>> abcStr = ('M:6/8\nL:1/8\nV: * clef=treble\nK:G\nV:1 name="Whistle" ' +
        ...     'snm="wh"\nB3 A3 | G6 | B3 A3 | G6 ||\nV:2 name="violin" ' +
        ...     'snm="v"\nBdB AcA | GAG D3 | BdB AcA | GAG D6 ||\nV:3 name="Bass" ' +
        ...     'snm="b" clef=bass\nD3 D3 | D6 | D3 D3 | D6 ||')
        >>> ah = abcFormat.ABCHandler()
        >>> ah.process(abcStr)
        >>> tokenColls = ah.splitByVoice()

        >>> [t.src for t in tokenColls[0].tokens]
        ['M:6/8', 'L:1/8', 'V: * clef=treble', 'K:G', 'V:1 name="Whistle" snm="wh"', 'B3', 'A3',
        '|', 'G6', '|', 'B3', 'A3', '|', 'G6', '||']
        >>> [t.src for t in tokenColls[1].tokens]
        ['M:6/8', 'L:1/8', 'V: * clef=treble', 'K:G', 'V:2 name="violin" snm="v"', 'B', 'd', 'B', 'A',
        'c', 'A', '|', 'G', 'A', 'G', 'D3', '|', 'B', 'd', 'B', 'A', 'c', 'A', '|', 'G', 'A', 'G', 'D6', '||']
        >>> [t.src for t in tokenColls[2].tokens]
        ['M:6/8', 'L:1/8', 'V: * clef=treble', 'K:G', 'V:3 name="Bass" snm="b" clef=bass',
        'D3', 'D3', '|', 'D6', '|', 'D3', 'D3', '|', 'D6', '||']
        """
        active_voice = []
        voices = {'1': active_voice}
        tokenIter = iter(self.tokens)
        voice_id = '1'

        header = []
        for token in tokenIter:
            if isinstance(token, ABCMetadata):
                header.append(token)
                if token.isKey():
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
            return [self]

        for token in tokenIter:
            if isinstance(token, ABCMetadata) and token.isVoice():
                voice_id = token.getVoiceData()['id']
                if voice_id is None or voice_id == '*':
                    # error in abc code, the voice has no id or
                    # is the 'every voice' id (illegal in body)
                    # skip this token
                    continue

                # change the active voice
                if voice_id in voices:
                    active_voice = voices[voice_id]
                else:
                    active_voice = []
                    voices[voice_id] = active_voice

            active_voice.append(token)

        voice_handler = []
        # Create a new Handler for each voice with the header tokens first.
        for voice_id, tokens in voices.items():
            vh = ABCHandler(tokens=header + tokens, abcVersion=self.abcVersion)
            if vh.hasNotes():
                voice_handler.append(vh)

        return voice_handler

    def splitByMeasure(self) -> List['ABCHandlerBar']:
        r'''
        Divide a token list by Measures, also
        defining start and end bars of each Measure.

        If a component does not have notes, leave
        as an empty bar. This is often done with leading metadata.

        Returns a list of ABCHandlerBar instances.
        The first usually defines only Metadata
        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('L:1/4\nK:C\nCG CG| FC FC | bACB')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCMetadata 'L:1/4'>, <music21.abcFormat.ABCMetadata 'K:C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        [<music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>]
        [<music21.abcFormat.ABCNote 'b'>, <music21.abcFormat.ABCNote 'A'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'B'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None None
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> None

        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('X:1\nL:1/4\nK:C\n|CG CG|]')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCMetadata 'X:1'>, <music21.abcFormat.ABCMetadata 'L:1/4'>, <music21.abcFormat.ABCMetadata 'K:C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|]'>

        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('L:1/4\nK:C\n||CG CG||||')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCMetadata 'L:1/4'>, <music21.abcFormat.ABCMetadata 'K:C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '||'>
        <music21.abcFormat.ABCBar '||'> <music21.abcFormat.ABCBar '||'>

        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('L:1/4\nK:C\n|CG CG|')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCMetadata 'L:1/4'>, <music21.abcFormat.ABCMetadata 'K:C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>

        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('L:1/4\nK:C\nCG CG| FC FC | bACB\nK:G\n CEG')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCMetadata 'L:1/4'>, <music21.abcFormat.ABCMetadata 'K:C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        [<music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'F'>, <music21.abcFormat.ABCNote 'C'>]
        [<music21.abcFormat.ABCNote 'b'>, <music21.abcFormat.ABCNote 'A'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'B'>, <music21.abcFormat.ABCMetadata 'K:G'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'E'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None None
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> None
        None None

        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('L:1/4\nK:C\n|CG CG|')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.tokens)
        [<music21.abcFormat.ABCMetadata 'L:1/4'>, <music21.abcFormat.ABCMetadata 'K:C'>]
        [<music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>, <music21.abcFormat.ABCNote 'C'>, <music21.abcFormat.ABCNote 'G'>]
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None <music21.abcFormat.ABCBar '|'>
        <music21.abcFormat.ABCBar '|'> <music21.abcFormat.ABCBar '|'>

        >>> ah = abcFormat.ABCHandler()
        >>> ah.process('L:1/4\nK:C\nCG CG||CG CG||')
        >>> mhl = ah.splitByMeasure()
        >>> for mh in mhl: print (mh.leftBarToken, mh.rightBarToken)
        None None
        None <music21.abcFormat.ABCBar '||'>
        <music21.abcFormat.ABCBar '||'> <music21.abcFormat.ABCBar '||'>
        '''

        from itertools import zip_longest
        def split_tokens():
            tokens = []
            for prev, t in zip_longest([None] + self.tokens, self.tokens):
                if t is None:
                    if tokens:
                        yield ABCHandlerBar(tokens)
                    return
                elif isinstance(t, ABCBar):
                    if tokens:
                        yield ABCHandlerBar(tokens)
                        tokens = []
                    yield t
                elif isinstance(t, ABCMetadata):
                    if not isinstance(prev, ABCBar):
                        tokens.append(t)
                    else:
                        if tokens:
                            yield ABCHandlerBar(tokens)
                        tokens = [t]
                elif isinstance(prev, ABCMetadata):
                    if tokens:
                        yield ABCHandlerBar(tokens)
                    tokens = [t]
                else:
                    tokens.append(t)

        last = None
        result = []
        for e in split_tokens():
            if isinstance(e, ABCBar):
                if isinstance(last, ABCHandlerBar):
                    last.rightBarToken = e
            else:
                result.append(e)
                # e is instance of HanlerBar
                if isinstance(last, ABCBar):
                    e.leftBarToken = last
            last = e

        return result

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

        return any(isinstance(t, ABCGeneralNote) for t in self.tokens)

    def getTitle(self) -> Optional[str]:
        '''
        Get the first title tag. Used for testing.

        Requires tokens to have been processed.
        '''
        if not self.tokens:
            raise ABCHandlerException('must process tokens before calling split')
        for t in self.tokens:
            if isinstance(t, ABCMetadata):
                if t.isTitle():
                    return t.data
        return None


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
        super().__init__()
        if tokens is not None:
            self.tokens = tokens
        self.leftBarToken: Optional[ABCBar] = left
        self.rightBarToken: Optional[ABCBar] = rigth

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
            (testFiles.fyrareprisarn, 242, 152, 0),
            (testFiles.mysteryReel, 198, 153, 0),
            (testFiles.aleIsDear, 291, 206, 32),
            (testFiles.testPrimitive, 102, 75, 2),
            (testFiles.williamAndNancy, 173, 93, 0),
            (testFiles.morrisonsJig, 178, 137, 0),
        ]:

            handler = ABCHandler()
            tokens = handler.tokenize(tf)  # get private for testing

            self.assertEqual(len(tokens), countTokens, tf)
            countNotes = 0
            countChords = 0
            countRest = 0
            for o in tokens:
                if isinstance(o, ABCChord):
                    countChords += 1
                elif isinstance(o, ABCNote):
                    countNotes += 1
                elif isinstance(o, ABCRest):
                    countRest += 1

            self.assertEqual(countNotes + countRest, noteTokens)

            self.assertEqual(countChords, chordTokens)

    def testRe(self):

        # @TODO: this test needs a new implementation
        # I have removed all here, because of relevanz
        pass

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
            handler.tokens = handler.tokenize(tf)
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
            handler.tokens = handler.tokenize(tf)
            # try:
            handler.tokenProcess()
            # except:
            #    breakpoint()

    def testNoteParse(self):
        from music21 import key
        # breakpoint()
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

        # self.assertEqual(len(ahm), 10)

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

        self.assertEqual(len(ah), 247)  # total tokens

        ahs = ah.splitByReferenceNumber()
        self.assertEqual(len(ahs), 3)
        self.assertEqual(sorted(list(ahs.keys())), [166, 167, 168])

        self.assertEqual(ahs[168].tokens[0].src, 'X:168')  # first is retained
        self.assertEqual(ahs[168].getTitle(), '168  The Castle Gate   (HJ)')
        self.assertEqual(len(ahs[168]), 90)  # tokens

        self.assertEqual(ahs[166].tokens[0].src, 'X:166')  # first is retained
        # noinspection SpellCheckingInspection
        self.assertEqual(ahs[166].getTitle(), '166  Valentine Jigg   (Pe)')
        self.assertEqual(len(ahs[166]), 68)  # tokens

        self.assertEqual(ahs[167].tokens[0].src, 'X:167')  # first is retained
        self.assertEqual(ahs[167].getTitle(), '167  The Dublin Jig     (HJ)')
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
        self.assertEqual(len(ah), 80)

    def testBow(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.bowTest)
        self.assertEqual(len(ah), 83)
        tokens = ah.tokens
        upbow = 0
        downbow = 0
        for t in tokens:
            if isinstance(t, ABCArticulation):
                if t.m21Class == articulations.UpBow:
                    upbow += 1
                elif t.m21Class == articulations.DownBow:
                    downbow += 1
        self.assertEqual(upbow, 2)
        self.assertEqual(downbow, 1)

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
<music21.abcFormat.ABCMetadata 'U: M = !tenuto!'>
<music21.abcFormat.ABCMetadata 'K: Edor'>
<music21.abcFormat.ABCNote 'B,2'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCDimStart '!diminuendo(!'>
<music21.abcFormat.ABCArticulation '.'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote '^D'>
<music21.abcFormat.ABCArticulation '.'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCTie '-'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCParenStop '!diminuendo)!'>
<music21.abcFormat.ABCSlurStart '('>
<music21.abcFormat.ABCTuplet '(3'>
<music21.abcFormat.ABCArticulation '.'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCArticulation '.'>
<music21.abcFormat.ABCNote 'F'>
<music21.abcFormat.ABCArticulation '.'>
<music21.abcFormat.ABCArticulation '!accent!'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCNote 'B'>
<music21.abcFormat.ABCNote 'A'>
<music21.abcFormat.ABCParenStop ')'>
<music21.abcFormat.ABCBar '|'>
<music21.abcFormat.ABCNote 'E'>
<music21.abcFormat.ABCNote '^D'>
<music21.abcFormat.ABCArticulation '!tenuto!'>
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
<music21.abcFormat.ABCArticulation '!straccent!'>
<music21.abcFormat.ABCArticulation '!tenuto!'>
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
<music21.abcFormat.ABCArticulation '!straccent!'>
<music21.abcFormat.ABCNote 'G'>
<music21.abcFormat.ABCArticulation '!accent!'>
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

        self.assertEqual(len(ah), 87)
        tokens = ah.tokens
        tenuto = 0
        straccent = 0
        accent = 0
        for t in tokens:
            if isinstance(t, abcFormat.ABCArticulation) and t.m21Class == articulations.StrongAccent:
                straccent += 1
            elif isinstance(t, abcFormat.ABCArticulation) and t.m21Class == articulations.Accent:
                accent += 1
            elif isinstance(t, abcFormat.ABCArticulation) and t.m21Class == articulations.Tenuto:
                tenuto += 1

        self.assertEqual(tenuto, 2)
        self.assertEqual(straccent, 2)
        self.assertEqual(accent, 2)

    def testGrace(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.graceTest)
        self.assertEqual(len(ah), 85)

    def testGuineaPig(self):
        from music21.abcFormat import testFiles
        ah = ABCHandler()
        ah.process(testFiles.guineapigTest)
        self.assertEqual(len(ah), 105)


def import_all_abc():
    import music21
    af = music21.abcFormat.ABCFile()
    for abc_file in pathlib.Path('../corpus/').glob('**/*.abc'):
        with abc_file.open() as f:
            af.readstr(f.read())


def benchmark():
    import timeit
    print(timeit.timeit(setup='from __main__ import import_all_abc', stmt='import_all_abc()', number=1))


# ------------------------------------------------------------------------------
# define presented order in documentation
_DOC_ORDER = [ABCFile, ABCHandler, ABCHandlerBar]

if __name__ == '__main__':
    import music21

    music21.mainTest(Test)
    # us = environment.UserSettings()
    # us['musicxmlPath'] = '/data/local/MuseScore-3.5.2.312125617-x86_64.AppImage'
    # sys.arg test options will be used in mainTest()
    with pathlib.Path('Unendliche_Freude.abc').open() as f:
        avem = f.read()

    #s = music21.converter.parse(avem)
    #s.show()

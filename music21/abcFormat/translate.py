# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# Name:         abcFormat.translate.py
# Purpose:      Translate ABC and music21 objects
#
# Authors:      Christopher Ariza
#               Michael Scott Cuthbert
#               Dylan Nagler
#
# Copyright:    Copyright © 2010-2013 Michael Scott Cuthbert and the music21
#               Project
# License:      BSD, see license.txt
# -----------------------------------------------------------------------------
'''
Functions for translating music21 objects and
:class:`~music21.abcFormat.ABCHandler` instances.
Mostly, these functions are for advanced, low level usage.
For basic importing of ABC files from a file or URL to a
:class:`~music21.stream.Stream`, use the music21 converter
module's :func:`~music21.converter.parse` function.
'''

import copy
import unittest
import re

from music21 import clef
from music21 import common
from music21 import environment
from music21 import exceptions21
from music21 import meter
from music21 import tempo
from music21 import stream
from music21 import note
from music21 import interval
from music21.duration import GraceDuration
from music21 import spanner
from music21 import harmony
from music21 import key
from music21 import instrument
from collections import defaultdict
from typing import Dict, Tuple, List, Union, Optional
from music21 import abcFormat


environLocal = environment.Environment('abcFormat.translate')


class ABCTranslateException(exceptions21.Music21Exception):
    pass

MIDI_RE = re.compile('voice\s*(?P<voice>.+)\s+instrument\s*=\s*(?P<instrument>.*)')
def get_midi_voice(instruction: str) -> Tuple[str, int]:
    match = MIDI_RE.match(instruction)
    if match:
        gd = match.groupdict()
        voice_id = gd['voice']
        midi_instrument = gd['instrument']
        if midi_instrument.isdigit():
            midi_instrument = instrument.instrumentFromMidiProgram(int(gd['instrument']))
        else:
            midi_instrument = instrument.fromString(midi_instrument)

        return voice_id, midi_instrument
    else:
        raise ABCTranslateException(f'Invalid midi instruction: "{instruction}"')


class ABCTranslator():

    def __init__(self):
        self.metadata = None

    def translate(self, handler: abcFormat.ABCHandler, target: stream.Stream):
        self.metadata = music21.metadata.Metadata()
        target.coreInsert(0, self.metadata)

        for token in handler.tokens:
            # find a transalte method for the token by class name
            token_translate_method = getattr(self, f'translate_{token.__class__.__name__}', None)

            # find a process method for the token by super class name
            if token_translate_method is None:
                for token_base_class in token.__class__.__bases__:
                    token_translate_method = f'translate_{token_base_class.__name__}'
                    if hasattr(self, token_translate_method):
                        token_translate_method = getattr(self, token_translate_method)
                        break
                else:
                    environLocal.printDebug([f'No translation method for token: "{token}" found.'])
                    continue

            if token_translate_method is not None:
                m21_object = token_translate_method(token)
                if m21_object is not None:
                    target.coreAppend(m21_object, setActiveSite=False)

    def translate_ABCTitle(self, token: abcFormat.ABCTitle):
        if self.metadata.title:
            self.metadata.alternativeTitle = token.data
        else:
            self.metadata.title = token.data

    def translate_ABCOrigin(self, token: abcFormat.ABCOrigin):
        self.metadata.localeOfComposition = token.data

    def translate_ABCComposer(self, token: abcFormat.ABCComposer):
        self.metadata.composers.append(token.data)

    def translate_ReferenceNumber(self, token: abcFormat.ABCReferenceNumber):
        # Convert referenceNumber to a number string
        self.metadataObject.number, _ = common.getNumFromStr(token.data)


class ABCHeaderTranslator(ABCTranslator):

    def __init__(self):
        self.voices : [str, abcFormat.ABCVoice] = {}
        self.midi = {}
        self.clef = Optional[clef.Clef] = None
        self.keySignature: Optional[key.KeySignature] = None
        self.metronomeMark = Optional[tempo.MetronomeMark] = None
        self.timeSignature = None
        self.octave: int = 0

    def translate_ABCVoice(self, token: abcFormat.ABCVoice):
        self.voices[token.id] = token

    def translate_ABCInstruction(self, token: abcFormat.ABCInstruction):
        if token.key.lower() == 'midi':
            try:
                voice_id, instrumentObject = get_midi_voice(token.instuction)
                self.midi['midi'] = instrumentObject
            except ABCTranslateException as e:
                environLocal.printDebug([e])

    def translate_ABCMeter(self, token: abcFormat.ABCMeter):
        self.timeSignature= token.getTimeSignatureObject()

    def translate_ABCKey(self, token: abcFormat.ABCKey):
        self.clef = token.clef
        self.octave = token.octave
        self.keySignature = token.getKeySignatureObject()

    def translate_ABCTempo(self, token: abcFormat.ABCTempo):
        self.metronomeMarker = token.getMetronomeMarkObject()


class ABCTokenTranslator(ABCTranslator):

    def __init__(self, parent: stream.Part):
        super().__init__()
        self.parent = parent
        self.clef = None
        self.timeSignature = None

    def translate(self, handler: abcFormat.ABCHandler, target: Union[stream.Measure, stream.Part]):
        super().translate(handler, target)
        if self.timeSignature:
            target.timeSignature = self.timeSignature
        if self.clef:
            target.timeSignature = self.clef

    def translate_ABCMeter(self, token: abcFormat.ABCMeter):
        self.timeSignature = token.getTimeSignatureObject()

    def translate_ABCKey(self, token: abcFormat.ABCKey):
        self.clef = token.clef
        self.octave = token.octave
        return token.getKeySignatureObject()

    def translate_ABCTempo(self, token: abcFormat.ABCTempo):
        return token.getMetronomeMarkObject()

    def translate_ABCGeneralNote(self, token: abcFormat.ABCGeneralNote):
        return token.m21Object()

    def translate_ABCMark(self, token: abcFormat.ABCMark):
        return token.m21Object()

    def translate_ABCSpanner(self, token: abcFormat.ABCSpanner):
        m21object = token.m21Object()
        if m21object:
            self.parent.coreInsert(0, m21object)


def abcToMeasure(abcBar: abcFormat.ABCHandlerBar, target: stream.Measure, spannerBundle, translator):

    translator.translate(abcBar, target=target)

    if abcBar.leftBarToken is not None:
        # this may be Repeat Bar subclass
        bLeft = abcBar.leftBarToken.m21Object()
        if bLeft is not None:
            target.leftBarline = bLeft

        if abcBar.leftBarToken.isRepeatBracket():
            # get any open spanners of RepeatBracket type
            rbSpanners = spannerBundle.getByClass('RepeatBracket'
                                                  ).getByCompleteStatus(False)
            # this indication is most likely an opening, as ABC does
            # not encode second ending ending boundaries
            # we can still check thought:
            if not rbSpanners:
                # add this measure as a component
                rb = spanner.RepeatBracket(target)
                # set number, returned here
                rb.number = abcBar.leftBarToken.isRepeatBracket()
                # only append if created; otherwise, already stored
                spannerBundle.append(rb)
            else:  # close it here
                rb = rbSpanners[0]  # get RepeatBracket
                rb.addSpannedElements(target)
                rb.completeStatus = True
                # this returns 1 or 2 depending on the repeat
            # in ABC, second repeats close immediately; that is
            # they never span more than one measure
            if abcBar.leftBarToken.isRepeatBracket() == 2:
                rb.completeStatus = True

    if abcBar.rightBarToken is not None:
        bRight = abcBar.rightBarToken.m21Object()
        if bRight is not None:
            target.rightBarline = bRight
        # above returns bars and repeats; we need to look if we just
        # have repeats
        if abcBar.rightBarToken.isRepeat():
            # if we have a right bar repeat, and a spanner repeat
            # bracket is open (even if just assigned above) we need
            # to close it now.
            # presently, now r bar conditions start a repeat bracket
            rbSpanners = spannerBundle.getByClass(
                'RepeatBracket').getByCompleteStatus(False)
            if any(rbSpanners):
                rb = rbSpanners[0]  # get RepeatBracket
                rb.addSpannedElements(target)
                rb.completeStatus = True
                # this returns 1 or 2 depending on the repeat
                # do not need to append; already in bundle


def abcToStreamPart(handler: abcFormat.ABCProcessHandler, inputM21: stream.Part) -> stream.Part:
    '''
    Handler conversion of a single Part of a multi-part score.
    Results are added into the provided inputM21 object
    or a newly created Part object

    The part object is then returned.
    '''

    m21Part = stream.Part() if inputM21 is None else inputM21
    spannerBundle = spanner.SpannerBundle()

    hasMeasures = handler.definesMeasures()
    if not hasMeasures:
        translator = ABCTokenTranslator(parent=m21Part)
        translator.translate(handler=handler, target=m21Part)
    else:
        translator = ABCTokenTranslator(parent=m21Part)
        translator.timeSignature = m21Part.timeSignature
        barHandlers = handler.splitByMeasure()

        for barNumber, abcBar in enumerate(barHandlers):
            m21Measure = stream.Measure()
            abcToMeasure(abcBar, m21Measure, spannerBundle, translator)

            # append measure to part; in the case of trailing meta data
            # dst may be part, even though useMeasures is True
            if 'Measure' not in m21Measure.classes:
                environLocal.warn(f'No "Measure" class found in "{m21Measure}"')
                continue

            # check for incomplete bars
            if barNumber == 0:
                if m21Part.timeSignature is not None:  # easy case
                    # can only do this b/c ts is defined
                    if m21Measure.barDurationProportion() < 1.0:
                        m21Measure.padAsAnacrusis()

            m21Measure.number = barNumber
            m21Part.coreAppend(m21Measure)

    m21Part.coreElementsChanged()

    try:
        reBar(m21Part, inPlace=True)
    except (ABCTranslateException, meter.MeterException, ZeroDivisionError):
        pass

    # clefs are not typically defined, but if so, are set to the first measure
    # following the meta data, or in the open stream
    """
    if not clefSet and not p.recurse().getElementsByClass('Clef'):
        if useMeasures:  # assume at start of measures
            p.getElementsByClass('Measure')[0].clef = clef.bestClef(p, recurse=True)
        else:
            p.coreInsert(0, clef.bestClef(p, recurse=True))
    """
    if hasMeasures and p.recurse().getElementsByClass('TimeSignature'):
        # call make beams for now; later, import beams
        # environLocal.printDebug(['abcToStreamPart: calling makeBeams'])
        try:
            m21Part.makeBeams(inPlace=True)
        except (meter.MeterException, stream.StreamException) as e:
            environLocal.warn(f'Error in beaming...ignoring: {e}')

    # copy spanners into topmost container; here, a part
    rm = []
    for sp in spannerBundle.getByCompleteStatus(True):
        m21Part.coreInsert(0, sp)
        rm.append(sp)
    # remove from original spanner bundle
    for sp in rm:
        spannerBundle.remove(sp)

    m21Part.coreElementsChanged()
    return m21Part


LYRIC_VERSES = []

"""
    for verse_number, verse in enumerate(LYRIC_VERSES):
        if verse:
            # look at the first syllable and skip the measure on '|'
            if verse[0] == '|':
                verse.pop(0)
                continue
        else:
            continue

        # start alligning syllable to notes
        for n in dst.notesAndRests:
            if isinstance(n, (harmony.ChordSymbol, note.Rest)) \
                or isinstance(n.duration, GraceDuration):
                # Do not allign syllables to Chordsymbols, Rests and Gracenotes
                continue

            if verse:
                syllable = verse[0]
                verse.pop(0)
            else:
                break

            # skip the measure
            if syllable == '|':
                break

            # previous syllable is to be held for an extra note
            if syllable == '_':
                continue

            if syllable == '*':
                n.lyrics.append(note.Lyric(number=verse_number, text=''))
            else:
                n.lyrics.append(note.Lyric(number=verse_number, text=syllable))


    if voice_id and voice_id in voice_data:
        voice_data = voice_data[voice_id]
        if 'MIDI' in voice_data:
            p.coreInsert(0.0, voice_data['MIDI'])
        if 'CLEF' in voice_data:
            p.coreInsert(0.0, voice_data['CLEF'])
        if 'TRANSPOSITION' in voice_data:
            postTransposition = voice_data['TRANSPOSITION']
"""

def abcToStreamScore(abcHandler, inputM21: stream.Score=None):
    '''
    Given an abcHandler object, build into a
    multi-part :class:`~music21.stream.Score` with metadata.

    This assumes that this ABCHandler defines a single work (with 1 or fewer reference numbers).

    if the optional parameter inputM21 is given a music21 Stream subclass, it will use that object
    as the outermost object.  However, inner parts will
    always be made :class:`~music21.stream.Part` objects.
    '''

    if inputM21 is None:
        s = stream.Score()
    else:
        s = inputM21

    # meta data can be first
    if abcHandler.metadataObject:

    md = abcHandler.metadataObject
    if md is None:
        md = metadata.Metadata()
    s.insert(0, md)

    # get title from large-scale metadata
    titleCount = 0
    for t in abcHandler.tokens:
        if isinstance(t, abcFormat.ABCTitle):
            if titleCount == 0:  # first
                md.title = t.data
                # environLocal.printDebug(['got metadata title', md.title])
                titleCount += 1
            # all other titles go in alternative field
        else:
            md.alternativeTitle = t.data
            # environLocal.printDebug(['got alternative title', md.alternativeTitle])
            titleCount += 1
        elif isinstance(t, ):
            md.composer = t.data

    # split the tune is voices (part)
    # each voice has leading metadata from the tune
    partHandlers = abcHandler.splitByVoice()

    # find if this token list defines measures
    # this should probably operate at the level of tunes, not the entire
    # token list

    partList = []
    for partHandler in partHandlers:
        p = abcToStreamPart(partHandler)
        partList.append(p)

    for p in partList:
        s.coreInsert(0, p)
    s.coreElementsChanged()
    return s


def abcToStreamOpus(abcHandler, inputM21=None, number=None):
    '''Convert a multi-work stream into one or more complete works packed into a an Opus Stream.

    If a `number` argument is given, and a work is defined by
    that number, that work is returned.
    '''
    if inputM21 is None:
        opus = stream.Opus()
    else:
        opus = inputM21

    # environLocal.printDebug(['abcToStreamOpus: got number', number])

    # returns a dictionary of numerical key
    if abcHandler.definesReferenceNumbers():
        abcDict = abcHandler.splitByReferenceNumber()
        if number is not None and number in abcDict:
            # get number from dictionary; set to new score
            opus = abcToStreamScore(abcDict[number])  # return a score, not an opus
        else:  # build entire opus into an opus stream
            scoreList = []
            for key in sorted(abcDict.keys()):
                # do not need to set work number, as that will be gathered
                # with meta data in abcToStreamScore
                try:
                    scoreList.append(abcToStreamScore(abcDict[key]))
                except IndexError:
                    environLocal.warn(f'Failure for piece number {key}')
            for scoreDocument in scoreList:
                opus.coreAppend(scoreDocument, setActiveSite=False)
            opus.coreElementsChanged()

    else:  # just return single entry in opus object
        opus.append(abcToStreamScore(abcHandler))
    return opus


# noinspection SpellCheckingInspection
def reBar(music21Part, *, inPlace=False):
    '''
    Re-bar overflow measures using the last known time signature.

    >>> irl2 = corpus.parse('irl', number=2, forceSource=True)
    >>> irl2.metadata.title
    'Aililiu na Gamhna, S.35'
    >>> music21Part = irl2[1]


    The whole part is in 2/4 time, but there are some measures expressed in 4/4 time
    without an explicit time signature change, an error in abc parsing due to the
    omission of barlines. The method will split those measures such that they conform
    to the last time signature, in this case 2/4. The default is to reBar in place.
    The measure numbers are updated accordingly.

    (NOTE: reBar is called automatically in abcToStreamPart, hence not demonstrated below...)

    The key signature and clef are assumed to be the same in the second measure after the
    split, so both are omitted. If the time signature is not the same in the second measure,
    the new time signature is indicated, and the measure following returns to the last time
    signature, except in the case that a new time signature is indicated.

    >>> music21Part.measure(15).show('text')
    {0.0} <music21.note.Note A>
    {1.0} <music21.note.Note A>

    >>> music21Part.measure(16).show('text')
    {0.0} <music21.note.Note A>
    {0.5} <music21.note.Note B->
    {1.0} <music21.note.Note A>
    {1.5} <music21.note.Note G>

    An example where the time signature wouldn't be the same. This score is
    mistakenly marked as 4/4, but has some measures that are longer.

    >>> irl15 = corpus.parse('irl', number=15, forceSource=True)
    >>> irl15.metadata.title
    'Esternowe, S. 60'
    >>> music21Part2 = irl15.parts[0]  # 4/4 time signature
    >>> music21Part2.measure(1).show('text')
    {0.0} <music21.note.Note C>
    {1.0} <music21.note.Note A>
    {1.5} <music21.note.Note G>
    {2.0} <music21.note.Note E>
    {2.5} <music21.note.Note G>
    >>> music21Part2.measure(1)[-1].duration.quarterLength
    1.5

    >>> music21Part2.measure(2).show('text')
    {0.0} <music21.meter.TimeSignature 1/8>
    {0.0} <music21.note.Note E>

    Changed in v.5: inPlace is False by default, and a keyword only argument.
    '''
    if not inPlace:
        music21Part = copy.deepcopy(music21Part)
    lastTimeSignature = None
    measureNumberOffset = 0  # amount to shift current measure numbers
    allMeasures = music21Part.getElementsByClass(stream.Measure)
    for measureIndex in range(len(allMeasures)):
        music21Measure = allMeasures[measureIndex]
        if music21Measure.timeSignature is not None:
            lastTimeSignature = music21Measure.timeSignature

        if lastTimeSignature is None:
            raise ABCTranslateException('No time signature found in this Part')

        tsEnd = lastTimeSignature.barDuration.quarterLength
        mEnd = common.opFrac(music21Measure.highestTime)
        music21Measure.number += measureNumberOffset
        if mEnd > tsEnd:
            m1, m2 = music21Measure.splitAtQuarterLength(tsEnd)
            m2.timeSignature = None
            if lastTimeSignature.barDuration.quarterLength != m2.highestTime:
                try:
                    m2.timeSignature = m2.bestTimeSignature()
                except exceptions21.StreamException as e:
                    raise ABCTranslateException(
                        f'Problem with measure {music21Measure.number} ({music21Measure!r}): {e}')
                if measureIndex != len(allMeasures) - 1:
                    if allMeasures[measureIndex + 1].timeSignature is None:
                        allMeasures[measureIndex + 1].timeSignature = lastTimeSignature
            m2.keySignature = None  # suppress the key signature
            m2.clef = None  # suppress the clef
            m2.number = m1.number + 1
            measureNumberOffset += 1
            music21Part.insert(common.opFrac(m1.offset + m1.highestTime), m2)

        # elif ((mEnd + music21Measure.paddingLeft) < tsEnd
        #       and measureIndex != len(allMeasures) - 1):
        #    The first and last measures are allowed to be incomplete
        #    music21Measure.timeSignature = music21Measure.bestTimeSignature()
        #    if allMeasures[measureIndex + 1].timeSignature is None:
        #        allMeasures[measureIndex + 1].timeSignature = lastTimeSignature
        #

    if not inPlace:
        return music21Part


# ------------------------------------------------------------------------------
class Test(unittest.TestCase):

    def testBasic(self):
        from music21 import abcFormat
        # from music21.abcFormat import testFiles

        # noinspection SpellCheckingInspection
        for tf in [
            # testFiles.fyrareprisarn,
            # testFiles.mysteryReel,
            # testFiles.aleIsDear,
            # testFiles.testPrimitive,
            # testFiles.fullRiggedShip,
            # testFiles.kitchGirl,
            # testFiles.morrisonsJig,
            # testFiles.hectorTheHero,
            # testFiles.williamAndNancy,
            # testFiles.theAleWifesDaughter,
            # testFiles.theBeggerBoy,
            # testFiles.theAleWifesDaughter,
            # testFiles.draughtOfAle,

            # testFiles.testPrimitiveTuplet,
            # testFiles.testPrimitivePolyphonic,

        ]:
            af = abcFormat.ABCFile()
            ah = af.readstr(tf)  # return handler, processes tokens
            s = abcToStreamScore(ah)
            s.show()
            # s.show('midi')

    def testGetMetaData(self):
        '''
        NB -- only title is checked. not meter or key
        '''

        from music21 import abcFormat
        from music21.abcFormat import testFiles

        for (tf, titleEncoded, unused_meterEncoded, unused_keyEncoded) in [
            (testFiles.fyrareprisarn, 'Fyrareprisarn', '3/4', 'F'),
            (testFiles.mysteryReel, 'Mystery Reel', 'C|', 'G'),
            (testFiles.aleIsDear, 'The Ale is Dear', '4/4', 'D', ),
            (testFiles.kitchGirl, 'Kitchen Girl', '4/4', 'D'),
            (testFiles.williamAndNancy, 'William and Nancy', '6/8', 'G'),
        ]:

            af = abcFormat.ABCFile()
            ah = af.readstr(tf)  # returns an ABCHandler object
            s = abcToStreamScore(ah)

            self.assertEqual(s.metadata.title, titleEncoded)

    def testChords(self):

        from music21 import abcFormat
        from music21.abcFormat import testFiles

        tf = testFiles.aleIsDear
        af = abcFormat.ABCFile()
        s = abcToStreamScore(af.readstr(tf))
        # s.show()
        self.assertEqual(len(s.parts), 2)
        self.assertEqual(len(s.parts[0].flat.notesAndRests), 111)
        self.assertEqual(len(s.parts[1].flat.notesAndRests), 127)

        # chords are defined in second part here
        self.assertEqual(len(s.parts[1].flat.getElementsByClass('Chord')), 32)

        # check pitches in chords; sharps are applied due to key signature
        match = [p.nameWithOctave for p in s.parts[1].flat.getElementsByClass(
            'Chord')[4].pitches]
        self.assertEqual(match, ['F#4', 'D4', 'B3'])

        match = [p.nameWithOctave for p in s.parts[1].flat.getElementsByClass(
            'Chord')[3].pitches]
        self.assertEqual(match, ['E4', 'C#4', 'A3'])

        # s.show()
        # s.show('midi')

    def testMultiVoice(self):

        from music21 import abcFormat
        from music21.abcFormat import testFiles

        tf = testFiles.testPrimitivePolyphonic

        af = abcFormat.ABCFile()
        s = abcToStreamScore(af.readstr(tf))

        self.assertEqual(len(s.parts), 3)
        # must flatten b/c  there are measures
        self.assertEqual(len(s.parts[0].flat.notesAndRests), 6)
        self.assertEqual(len(s.parts[1].flat.notesAndRests), 17)
        self.assertEqual(len(s.parts[2].flat.notesAndRests), 6)

        # s.show()
        # s.show('midi')

    def testTuplets(self):

        from music21 import abcFormat
        from music21.abcFormat import testFiles

        tf = testFiles.testPrimitiveTuplet
        af = abcFormat.ABCFile()
        s = abcToStreamScore(af.readstr(tf))
        match = []
        # match strings for better comparison
        for n in s.flat.notesAndRests:
            match.append(n.quarterLength)
        shouldFind = [
            1 / 3, 1 / 3, 1 / 3,
            1 / 5, 1 / 5, 1 / 5, 1 / 5, 1 / 5,
            1 / 6, 1 / 6, 1 / 6, 1 / 6, 1 / 6, 1 / 6,
            1 / 7, 1 / 7, 1 / 7, 1 / 7, 1 / 7, 1 / 7, 1 / 7,
            2 / 3, 2 / 3, 2 / 3, 2 / 3, 2 / 3, 2 / 3,
            1 / 12, 1 / 12, 1 / 12, 1 / 12, 1 / 12, 1 / 12,
            1 / 12, 1 / 12, 1 / 12, 1 / 12, 1 / 12, 1 / 12,
            2
        ]
        self.assertEqual(match, [common.opFrac(x) for x in shouldFind])

    def testAnacrusisPadding(self):
        from music21 import abcFormat
        from music21.abcFormat import testFiles

        # 2 quarter pickup in 3/4
        ah = abcFormat.ABCHandler()
        ah.process(testFiles.hectorTheHero)
        s = abcToStreamScore(ah)
        m1 = s.parts[0].getElementsByClass('Measure')[0]
        # s.show()
        # ts is 3/4
        self.assertEqual(m1.barDuration.quarterLength, 3.0)
        # filled with two quarter notes
        self.assertEqual(m1.duration.quarterLength, 2.0)
        # m1.show('t')
        # notes are shown as being on beat 2 and 3
        # environLocal.printDebug(['m1.notesAndRests.activeSite', m1.notesAndRests.activeSite])
        # environLocal.printDebug(['m1.notesAndRests[0].activeSite',
        #     m1.notesAndRests[0].activeSite])

        # self.assertEqual(m1.notesAndRests.activeSite)

        n0 = m1.notesAndRests[0]
        n1 = m1.notesAndRests[1]
        self.assertEqual(n0.getOffsetBySite(m1) + m1.paddingLeft, 1.0)
        self.assertEqual(m1.notesAndRests[0].beat, 2.0)
        self.assertEqual(n1.getOffsetBySite(m1) + m1.paddingLeft, 2.0)
        self.assertEqual(m1.notesAndRests[1].beat, 3.0)

        # two 16th pickup in 4/4
        ah = abcFormat.ABCHandler()
        ah.process(testFiles.theAleWifesDaughter)
        s = abcToStreamScore(ah)
        m1 = s.parts[0].getElementsByClass('Measure')[0]

        # ts is 3/4
        self.assertEqual(m1.barDuration.quarterLength, 4.0)
        # filled with two 16th
        self.assertEqual(m1.duration.quarterLength, 0.5)
        # notes are shown as being on beat 2 and 3
        n0 = m1.notesAndRests[0]
        n1 = m1.notesAndRests[1]

        self.assertEqual(n0.getOffsetBySite(m1) + m1.paddingLeft, 3.5)
        self.assertEqual(m1.notesAndRests[0].beat, 4.5)
        self.assertEqual(n1.getOffsetBySite(m1) + m1.paddingLeft, 3.75)
        self.assertEqual(m1.notesAndRests[1].beat, 4.75)

    def testOpusImport(self):
        from music21 import corpus
        from music21 import abcFormat

        # replace w/ ballad80, smaller or erk5
        fp = corpus.getWork('essenFolksong/teste')
        self.assertEqual(fp.name, 'teste.abc')
        self.assertEqual(fp.parent.name, 'essenFolksong')

        af = abcFormat.ABCFile()
        af.open(fp)  # return handler, processes tokens
        ah = af.read()
        af.close()

        op = abcToStreamOpus(ah)
        # op.scores[3].show()
        self.assertEqual(len(op), 8)

    def testLyrics(self):
        # TODO(msc) -- test better

        from music21 import abcFormat
        from music21.abcFormat import testFiles

        tf = testFiles.sicutRosa
        af = abcFormat.ABCFile()
        s = abcToStreamScore(af.readstr(tf))
        assert s is not None

        # s.show()
#         self.assertEqual(len(s.parts), 3)
#         self.assertEqual(len(s.parts[0].notesAndRests), 6)
#         self.assertEqual(len(s.parts[1].notesAndRests), 20)
#         self.assertEqual(len(s.parts[2].notesAndRests), 6)
#
        # s.show()
        # s.show('midi')

    def testMultiWorkImported(self):

        from music21 import corpus
        # defines multiple works, will return an opus
        o = corpus.parse('josquin/milleRegrets')
        self.assertEqual(len(o), 4)
        # each score in the opus is a Stream that contains a Part and metadata
        p1 = o.getScoreByNumber(1).parts[0]
        self.assertEqual(p1.offset, 0.0)
        f = p1.flat.notesAndRests
        self.assertEqual(len(p1.flat.notesAndRests), 91)

        p2 = o.getScoreByNumber(2).parts[0]
        self.assertEqual(p2.offset, 0.0)
        self.assertEqual(len(p2.flat.notesAndRests), 81)

        p3 = o.getScoreByNumber(3).parts[0]
        self.assertEqual(p3.offset, 0.0)
        self.assertEqual(len(p3.flat.notesAndRests), 87)

        p4 = o.getScoreByNumber(4).parts[0]
        self.assertEqual(p4.offset, 0.0)
        self.assertEqual(len(p4.flat.notesAndRests), 79)

        sMerged = o.mergeScores()
        self.assertEqual(sMerged.metadata.title, 'Mille regrets')
        self.assertEqual(sMerged.metadata.composer, 'Josquin des Prez')
        self.assertEqual(len(sMerged.parts), 4)

        self.assertEqual(sMerged.parts[0].getElementsByClass('Clef')[0].sign, 'G')
        self.assertEqual(sMerged.parts[1].getElementsByClass('Clef')[0].sign, 'G')
        self.assertEqual(sMerged.parts[2].getElementsByClass('Clef')[0].sign, 'G')
        self.assertEqual(sMerged.parts[2].getElementsByClass('Clef')[0].octaveChange, -1)
        self.assertEqual(sMerged.parts[3].getElementsByClass('Clef')[0].sign, 'F')

        # sMerged.show()

    def testChordSymbols(self):
        from music21 import corpus, pitch
        # noinspection SpellCheckingInspection
        o = corpus.parse('nottingham-dataset/reelsa-c')
        self.assertEqual(len(o), 2)
        # each score in the opus is a Stream that contains a Part and metadata

        p1 = o.getScoreByNumber(81).parts[0]
        self.assertEqual(p1.offset, 0.0)
        self.assertEqual(len(p1.flat.notesAndRests), 77)
        self.assertEqual(len(list(p1.flat.getElementsByClass('ChordSymbol'))), 25)
        # Am/C
        self.assertEqual(list(p1.flat.getElementsByClass('ChordSymbol'))[7].root(),
                         pitch.Pitch('A3'))
        self.assertEqual(list(p1.flat.getElementsByClass('ChordSymbol'))[7].bass(),
                         pitch.Pitch('C3'))
        # G7/B
        self.assertEqual(list(p1.flat.getElementsByClass('ChordSymbol'))[14].root(),
                         pitch.Pitch('G3'))
        self.assertEqual(list(p1.flat.getElementsByClass('ChordSymbol'))[14].bass(),
                         pitch.Pitch('B2'))

    def testNoChord(self):

        from music21 import converter

        target_str = '''
            T: No Chords
            M: 4/4
            L: 1/1
            K: C
            [| "C" C | "NC" C | "C" C | "N.C." C | "C" C
            | "No Chord" C | "C" C | "None" C | "C" C | "Other"
            C |]
            '''
        score = converter.parse(target_str, format='abc')

        self.assertEqual(len(list(score.flat.getElementsByClass(
            'ChordSymbol'))), 9)
        self.assertEqual(len(list(score.flat.getElementsByClass(
            'NoChord'))), 4)

        score = harmony.realizeChordSymbolDurations(score)

        self.assertEqual(8, score.getElementsByClass('ChordSymbol')[
            -1].quarterLength)
        self.assertEqual(4, score.getElementsByClass('ChordSymbol')[
            0].quarterLength)

    def testAbcKeyImport(self):
        from music21 import abcFormat

        # sharps
        major = ['C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#']
        minor = ['Am', 'Em', 'Bm', 'F#m', 'C#m', 'G#m', 'D#m', 'A#m']

        for n, (majName, minName) in enumerate(zip(major, minor)):
            am = abcFormat.ABCMetadata('K:' + majName)
            ks_major = am.getKeySignatureObject()
            am = abcFormat.ABCMetadata('K:' + minName)
            ks_minor = am.getKeySignatureObject()
            self.assertEqual(n, ks_major.sharps)
            self.assertEqual(n, ks_minor.sharps)
            self.assertEqual('major', ks_major.mode)
            self.assertEqual('minor', ks_minor.mode)

        # flats
        major = ['C', 'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb']
        minor = ['Am', 'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm', 'Abm']

        for n, (majName, minName) in enumerate(zip(major, minor)):
            am = abcFormat.ABCMetadata('K:' + majName)
            ks_major = am.getKeySignatureObject()
            am = abcFormat.ABCMetadata('K:' + minName)
            ks_minor = am.getKeySignatureObject()
            self.assertEqual(-1 * n, ks_major.sharps)
            self.assertEqual(-1 * n, ks_minor.sharps)
            self.assertEqual('major', ks_major.mode)
            self.assertEqual('minor', ks_minor.mode)

    # noinspection SpellCheckingInspection
    def testLocaleOfCompositionImport(self):
        from music21 import corpus
        # defines multiple works, will return an opus
        o = corpus.parse('essenFolksong/teste')
        self.assertEqual(len(o), 8)

        s = o.getScoreByNumber(4)
        self.assertEqual(s.metadata.localeOfComposition, 'Asien, Ostasien, China, Sichuan')

        s = o.getScoreByNumber(7)
        self.assertEqual(s.metadata.localeOfComposition, 'Amerika, Mittelamerika, Mexiko')

    def testRepeatBracketsA(self):
        from music21.abcFormat import testFiles
        from music21 import converter
        s = converter.parse(testFiles.morrisonsJig)
        # s.show()
        # one start, one end
        # s.parts[0].show('t')
        self.assertEqual(len(s.flat.getElementsByClass('Repeat')), 2)
        # s.show()

        # this has a 1 note pickup
        # has three repeat bars; first one is implied
        s = converter.parse(testFiles.draughtOfAle)
        self.assertEqual(len(s.flat.getElementsByClass('Repeat')), 3)
        self.assertEqual(s.parts[0].getElementsByClass(
            'Measure')[0].notes[0].pitch.nameWithOctave, 'D4')

        # new problem case:
        s = converter.parse(testFiles.hectorTheHero)
        # first measure has 2 pickup notes
        self.assertEqual(len(s.parts[0].getElementsByClass('Measure')[0].notes), 2)

    def testRepeatBracketsB(self):
        from music21.abcFormat import testFiles
        from music21 import converter
        from music21 import corpus
        s = converter.parse(testFiles.morrisonsJig)
        # TODO: get
        self.assertEqual(len(s.flat.getElementsByClass('RepeatBracket')), 2)
        # s.show()
        # four repeat brackets here; 2 at beginning, 2 at end
        s = converter.parse(testFiles.hectorTheHero)
        self.assertEqual(len(s.flat.getElementsByClass('RepeatBracket')), 4)

        s = corpus.parse('JollyTinkersReel')
        self.assertEqual(len(s.flat.getElementsByClass('RepeatBracket')), 4)

    def testMetronomeMarkA(self):
        from music21.abcFormat import testFiles
        from music21 import converter
        s = converter.parse(testFiles.fullRiggedShip)
        mmStream = s.flat.getElementsByClass('TempoIndication')
        self.assertEqual(len(mmStream), 1)
        self.assertEqual(str(mmStream[0]), '<music21.tempo.MetronomeMark Quarter=100.0>')

        s = converter.parse(testFiles.aleIsDear)
        mmStream = s.flat.getElementsByClass('TempoIndication')
        # this is a two-part pieces, and this is being added for each part
        # not sure if this is a problem
        self.assertEqual(len(mmStream), 2)
        self.assertEqual(str(mmStream[0]), '<music21.tempo.MetronomeMark Quarter=211.0>')

        s = converter.parse(testFiles.theBeggerBoy)
        mmStream = s.flat.getElementsByClass('TempoIndication')
        # this is a two-part pieces, and this is being added for each part
        # not sure if this is a problem
        self.assertEqual(len(mmStream), 1)
        self.assertEqual(str(mmStream[0]), '<music21.tempo.MetronomeMark maestoso Quarter=90.0>')

        # s.show()

    def testTranslateA(self):
        # this tests a few files in this collection, some of which are hard to
        # parse
        from music21 import corpus
        # noinspection SpellCheckingInspection
        for fn in (
            'ToCashellImGoingJig.abc',
            'SundayIsMyWeddingDayJig.abc',
            'SinkHimDoddieHighlandFling.abc',
            'RandyWifeOfGreenlawReel.abc',
            'PassionFlowerHornpipe.abc',
            'NightingaleClog.abc',
            'MountainRangerHornpipe.abc',
            'LadiesPandelettsReel.abc',
            'JauntingCarHornpipe.abc',
            'GoodMorrowToYourNightCapJig.abc',
            'ChandlersHornpipe.abc',
            'AlistairMaclalastairStrathspey.abc',
        ):
            s = corpus.parse(fn)
            assert s is not None
            # s.show()

    def testCleanFlat(self):
        from music21 import pitch

        cs = harmony.ChordSymbol(root='eb', bass='bb', kind='dominant')
        self.assertEqual(cs.bass(), pitch.Pitch('B-2'))
        self.assertIs(cs.pitches[0], cs.bass())

        cs = harmony.ChordSymbol('e-7/b-')
        self.assertEqual(cs.root(), pitch.Pitch('E-3'))
        self.assertEqual(cs.bass(), pitch.Pitch('B-2'))
        self.assertEqual(cs.pitches[0], pitch.Pitch('B-2'))

        # common.cleanedFlatNotation() shouldn't be called by
        # the following calls, which what is being tested here:

        cs = harmony.ChordSymbol('b-3')
        self.assertEqual(cs.root(), pitch.Pitch('b-3'))
        self.assertEqual(cs.pitches[0], pitch.Pitch('B-3'))
        self.assertEqual(cs.pitches[1], pitch.Pitch('D4'))

        cs = harmony.ChordSymbol('bb3')
        # B, not B-flat
        self.assertEqual(cs.root(), pitch.Pitch('b2'))
        # b3 alteration applied to B major triad
        self.assertEqual(cs.pitches[0], pitch.Pitch('B2'))
        self.assertEqual(cs.pitches[1], pitch.Pitch('D3'))
        self.assertEqual(cs.pitches[2], pitch.Pitch('F#3'))

    def xtestTranslateB(self):
        '''
        Dylan -- this could be too slow to make it a test!

        Numbers 637 and 749 fail
        '''

        from music21 import corpus
        for fn in ['airdsAirs/book4.abc']:
            s = corpus.parse(fn)
            assert s is not None

            # s.show()

    def testTranslateBrokenDuration(self):
        from music21 import corpus
        unused = corpus.parse('han2.abc', number=445)

    def testTiesTranslate(self):
        from music21 import converter
        notes = converter.parse('L:1/8\na-a-a', format='abc')
        ties = [n.tie.type for n in notes.flat.notesAndRests]
        self.assertListEqual(ties, ['start', 'continue', 'stop'])

    def xtestMergeScores(self):
        from music21 import corpus
        unused = corpus.parse('josquin/laDeplorationDeLaMorteDeJohannesOckeghem')
        # this was getting incorrect Clefs...


if __name__ == '__main__':
    import music21

    irl2 = music21.corpus.parse('irl', number=2,forceSource=True)

    music21.mainTest(Test)



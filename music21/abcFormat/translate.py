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
from music21 import stream
from music21 import spanner
from music21 import harmony


environLocal = environment.Environment('abcFormat.translate')

def add_lyric(p: stream.Stream, abcHandler: 'abcFormat.ABCHandler'):
    """
    Adds Lyrik from the tokens of the abcHandler to the notes of the
    Stream.
    """
    from music21 import abcFormat

    lyric = (j for i in (t.getLyric() for t in abcHandler.tokens
                         if isinstance(t, abcFormat.ABCMetadata)
                         and t.isLyric())
             for j in i)

    for e in p.flat.notes:
        if isinstance(e, harmony.ChordSymbol):
            continue
        try:
            syl = next(lyric).strip()
            while not syl:
                syl = next(lyric).strip()
                # skip empty words
                if syl:
                    break
            if syl == '_':
                continue
            if syl == '*':
                # a blank syllable
                continue
            e.lyric = syl
        except StopIteration:
            # No lyric is left, stop
            break

def abcToStreamPart(voice_handler, inputM21=None, spannerBundle=None):
    '''
    Handler conversion of a single Part of a multi-part score.
    Results are added into the provided inputM21 object
    or a newly created Part object

    The part object is then returned.
    '''
    from music21.abcFormat import handler
    from music21 import  abcFormat
    if inputM21 is None:
        p = stream.Part()
    else:
        p = inputM21

    if spannerBundle is None:
        # environLocal.printDebug(['mxToMeasure()', 'creating SpannerBundle'])
        spannerBundle = spanner.SpannerBundle()

    clefSet = None
    postTransposition = 0

    # need to call on entire handlers, as looks for special criteria,
    # like that at least 2 regular bars are used, not just double bars
    if voice_handler.measures:
        barHandlers = voice_handler.measures

    mergedHandlers = voice_handler.measures

    # each unit in merged handlers defines possible a Measure (w/ or w/o metadata),
    # trailing meta data, or a single collection of metadata and note data

    barCount = 0
    measureNumber = 1
    # merged handler are ABCHandlerBar objects, defining attributes for barlines

    for mh in voice_handler.measures:
        # if use measures and the handler has notes; otherwise add to part
        # environLocal.printDebug(['abcToStreamPart', 'handler', 'left:', mh.leftBarToken,
        #    'right:', mh.rightBarToken, 'len(mh)', len(mh)])

        if mh.hasNotes():
            dst = stream.Measure()

            if mh.leftBar is not None:
                # this may be Repeat Bar subclass
                bLeft = mh.leftBar.ms21Object()
                if bLeft is not None:
                    dst.leftBarline = bLeft
                if mh.leftBar.isRepeatBracket():
                    # get any open spanners of RepeatBracket type
                    rbSpanners = spannerBundle.getByClass('RepeatBracket'
                                                          ).getByCompleteStatus(False)
                    # this indication is most likely an opening, as ABC does
                    # not encode second ending ending boundaries
                    # we can still check thought:
                    if not rbSpanners:
                        # add this measure as a component
                        rb = spanner.RepeatBracket(dst)
                        # set number, returned here
                        rb.number = mh.leftBar.isRepeatBracket()
                        # only append if created; otherwise, already stored
                        spannerBundle.append(rb)
                    else:  # close it here
                        rb = rbSpanners[0]  # get RepeatBracket
                        rb.addSpannedElements(dst)
                        rb.completeStatus = True
                        # this returns 1 or 2 depending on the repeat
                    # in ABC, second repeats close immediately; that is
                    # they never span more than one measure
                    if mh.leftBar.isRepeatBracket() == 2:
                        rb.completeStatus = True

            if mh.rightBar is not None:
                bRight = mh.rightBar.ms21Object()
                if bRight is not None:
                    dst.rightBarline = bRight
                # above returns bars and repeats; we need to look if we just
                # have repeats
                if mh.rightBar.isRepeat():
                    # if we have a right bar repeat, and a spanner repeat
                    # bracket is open (even if just assigned above) we need
                    # to close it now.
                    # presently, now r bar conditions start a repeat bracket
                    rbSpanners = spannerBundle.getByClass(
                        'RepeatBracket').getByCompleteStatus(False)
                    if any(rbSpanners):
                        rb = rbSpanners[0]  # get RepeatBracket
                        rb.addSpannedElements(dst)
                        rb.completeStatus = True
        else:
            dst = p  # store directly in a part instance

        # environLocal.printDebug([mh, 'dst', dst])
        # ql = 0  # might not be zero if there is a pickup

        postTransposition, clefSet = parseTokens(mh, dst, p, len(voice_handler.measures) > 0)

        # append measure to part; in the case of trailing meta data
        # dst may be part, even though useMeasures is True
        if voice_handler.measures and 'Measure' in dst.classes:
            # check for incomplete bars
            # must have a time signature in this bar, or defined recently
            # could use getTimeSignatures() on Stream

            if len(voice_handler.measures) == 1 and dst.timeSignature is not None:  # easy case
                # can only do this b/c ts is defined
                if dst.barDurationProportion() < 1.0:
                    dst.padAsAnacrusis()
                    dst.number = 0
                    # environLocal.printDebug([
                    #    'incompletely filled Measure found on abc import; ',
                    #    'interpreting as a anacrusis:', 'paddingLeft:', dst.paddingLeft])
            else:
                dst.number = measureNumber
                measureNumber += 1
            p.coreAppend(dst)

    try:
        reBar(p, inPlace=True)
    except (ABCTranslateException, meter.MeterException, ZeroDivisionError):
        pass
    # clefs are not typically defined, but if so, are set to the first measure
    # following the meta data, or in the open stream
    if not clefSet and not p.recurse().getElementsByClass('Clef'):
        if voice_handler.measures:  # assume at start of measures
            p.getElementsByClass('Measure')[0].clef = clef.bestClef(p, recurse=True)
        else:
            p.coreInsert(0, clef.bestClef(p, recurse=True))

    if postTransposition != 0:
        p.transpose(postTransposition, inPlace=True)

    if voice_handler.measures and p.recurse().getElementsByClass('TimeSignature'):
        # call make beams for now; later, import beams
        # environLocal.printDebug(['abcToStreamPart: calling makeBeams'])
        try:
            p.makeBeams(inPlace=True)
        except (meter.MeterException, stream.StreamException) as e:
            environLocal.warn(f'Error in beaming...ignoring: {e}')

    # copy spanners into topmost container; here, a part
    rm = []
    for sp in spannerBundle.getByCompleteStatus(True):
        p.coreInsert(0, sp)
        rm.append(sp)
    # remove from original spanner bundle
    for sp in rm:
        spannerBundle.remove(sp)

    #add_lyric(p, abcHandler)
    p.coreElementsChanged()
    return p


def parseTokens(mh, dst, p, useMeasures):
    '''
    parses all the tokens in a measure or part.
    '''
    # in case need to transpose due to clef indication
    from music21 import abcFormat
    from music21.abcFormat import tokens
    postTransposition = 0
    clefSet = False
    for t in mh.tokens:
        if isinstance(t, tokens.ABCMeterField):
            ts = t.m21Object()
            if ts is not None:  # can be None
                if useMeasures:  # assume at start of measures
                    dst.timeSignature = ts
                else:
                    dst.coreAppend(ts)
        elif isinstance(t, tokens.ABCKeyField):
            ks = t.m21Object()
            if useMeasures:  # assume at start of measures
                dst.keySignature = ks
            else:
                dst.coreAppend(ks)

            # @todo
            # check for clef information sometimes stored in key
            #try:
            #    clefObj, transposition = t.getClefObject()
            #except:
            #    pass
            #    if clefObj is not None:
            #        clefSet = False
            #        # environLocal.printDebug(['found clef in key token:', t,
            #        #     clefObj, transposition])
            #        if useMeasures:  # assume at start of measures
            #            dst.clef = clefObj
            #        else:
            #            dst.coreAppend(clefObj)
            #        postTransposition = transposition
        elif isinstance(t, tokens.ABCTempoField):
            mmObj = t.m21Object()
            dst.coreAppend(mmObj)
        elif isinstance(t, (tokens.ABCChordSymbol, tokens.ABCGeneralNote, tokens.ABCSpanner)):
            obj = t.m21Object()
            if obj is not None:
                dst.coreAppend(obj, setActiveSite=False)

    dst.coreElementsChanged()
    return postTransposition, clefSet

from music21.abcFormat.handler import ABCTuneBook
def abcToStreamScore(tune_book: ABCTuneBook, inputM21=None):
    '''
    Given an abcHandler object, build into a
    multi-part :class:`~music21.stream.Score` with metadata.

    This assumes that this ABCHandler defines a single work (with 1 or fewer reference numbers).

    if the optional parameter inputM21 is given a music21 Stream subclass, it will use that object
    as the outermost object.  However, inner parts will
    always be made :class:`~music21.stream.Part` objects.
    '''
    from music21 import abcFormat
    from music21 import metadata

    if inputM21 is None:
        s = stream.Score()
    else:
        s = inputM21

    abcHandler = list(tune_book.tunes.values())[0]
    # meta data can be first
    md = abcFormat.handler.abc_header_to_metadata(abcHandler.header)
    s.insert(0, md)

    voices = []

    for voice in abcHandler.voices.values():
        p = abcToStreamPart(voice)
        voices.append(p)

    for voice in voices:
        s.coreInsert(0, voice)

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

    >>> irl2 = corpus.parse('irl', number=2)
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

    >>> irl15 = corpus.parse('irl', number=15)
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


class ABCTranslateException(exceptions21.Music21Exception):
    pass


if __name__ == '__main__':
    import music21

    environment.set('debug', True)
    #Test().testTuplets()
    from pathlib import Path
    with Path('avemaria.abc').open() as f:
        avemaria = f.read()
    from music21 import converter
    s = converter.parse(avemaria)
    s.show()
    #music21.mainTest(Test)



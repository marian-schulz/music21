# -*- coding: utf-8 -*-
#------------------------------------------------------------------------------
# Name:         stream/iterator.py
# Purpose:      classes for walking through streams and filtering...
#
# Authors:      Michael Scott Cuthbert
#               Christopher Ariza
#
# Copyright:    Copyright © 2008-2015 Michael Scott Cuthbert and the music21 Project
# License:      LGPL or BSD, see license.txt
#------------------------------------------------------------------------------
'''
this class contains iterators and filters for walking through streams

StreamIterators are explicitly allowed to access private methods on streams.
'''
import unittest

from music21 import common
from music21.stream import filter
#from music21.exceptions21 import StreamException

#------------------------------------------------------------------------------

class StreamIterator(object):
    '''
    An Iterator object used to handle getting items from Streams.
    The :meth:`~music21.stream.Stream.__iter__` method
    returns this object, passing a reference to self.

    Note that this iterator automatically sets the active site of
    returned elements to the source Stream.

    Sets:

    * StreamIterator.srcStream -- the Stream iterated over
    * StreamIterator.index -- current index item
    * StreamIterator.streamLength -- length of elements.
    
    * StreamIterator.srcStreamElements -- srcStream._elements
    * StreamIterator.cleanupOnStop -- should the StreamIterator delete the
      reference to srcStream and srcStreamElements when stopping? default
      False

    '''
    def __init__(self, srcStream, filters=None, restoreActiveSites=True):
        self.srcStream = srcStream
        self.index = 0
        
        # use this so that it is sorted...
        self.srcStreamElements = srcStream.elements
        self.streamLength = len(self.srcStreamElements)

        self.cleanupOnStop = False
        self.restoreActiveSites = restoreActiveSites

        if filters is None:
            filters = []
        elif not common.isIterable(filters):
            filters = [filters]
        elif isinstance(filters, tuple) or isinstance(filters, set):
            filters = list(filters) # mutable....
        
        # self.filters is a list of expressions that
        # return True or False for an element for
        # whether it should be yielded.
        self.filters = filters

    def __iter__(self):
        self.index = 0
        return self
        
        
    def __next__(self):
        if self.index >= self.streamLength:
            self.cleanup()
            raise StopIteration
        
        self.index += 1 # increment early in case of an error.
    
        try:
            e = self.srcStreamElements[self.index - 1]
        except IndexError:
            # this may happen in the number of elements has changed
            return self.__next__()

        if self.matchesFilters(e) is False:
            return self.__next__()            
        
        if self.restoreActiveSites is True:
            e.activeSite = self.srcStream

        return e
        
    next = __next__

    def matchesFilters(self, e):
        '''
        returns False if any filter returns False, True otherwise.
        '''
        for f in self.filters:
            if f(e, self) is False:
                return False
        return True

    
    def cleanup(self):
        '''
        stop iteration; and cleanup if need be.
        '''
        if self.cleanupOnStop is not False:
            del self.srcStream
            del self.srcStreamElements
            self.srcStream = None
            self.srcStreamElements = ()
    
    
    def __getitem__(self, k):
        '''
        if you are in the iterator, you should still be able to request other items...
        uses self.srcStream.__getitem__

        >>> s = stream.Stream()
        >>> s.insert(0, note.Note('F#'))
        >>> s.repeatAppend(note.Note('C'), 2)
        >>> sI = s.__iter__()
        >>> sI
        <music21.stream.iterator.StreamIterator object at 0x...>
        >>> sI.srcStream is s
        True


        >>> for n in sI:
        ...    printer = (repr(n), repr(sI[0]))
        ...    print(printer)
        ('<music21.note.Note F#>', '<music21.note.Note F#>')
        ('<music21.note.Note C>', '<music21.note.Note F#>')
        ('<music21.note.Note C>', '<music21.note.Note F#>')
        >>> sI.srcStream is s
        True

        Demo of cleanupOnStop = True

        >>> sI.cleanupOnStop = True
        >>> for n in sI:
        ...    printer = (repr(n), repr(sI[0]))
        ...    print(printer)
        ('<music21.note.Note F#>', '<music21.note.Note F#>')
        ('<music21.note.Note C>', '<music21.note.Note F#>')
        ('<music21.note.Note C>', '<music21.note.Note F#>')
        >>> sI.srcStream is None
        True
        >>> for n in sI:
        ...    printer = (repr(n), repr(sI[0]))
        ...    print(printer)

        (nothing is printed)

        '''
        # TODO: apply to filters!
        return self.srcStream.__getitem__(k)
    
    #-------------------------------------------------------------
    def addFilter(self, newFilter):
        if newFilter not in self.filters:
            self.filters.append(newFilter)
        return self
    
    def getElementsByClass(self, classFilterList):
        '''
        Add a filter to the Iterator to remove all elements
        except those that match one
        or more classes in the `classFilterList`. A single class
        can also used for the `classFilterList` parameter instead of a List.

        >>> s = stream.Stream(id="s1")
        >>> s.append(note.Note('C'))
        >>> r = note.Rest()
        >>> s.append(r)
        >>> s.append(note.Note('D'))
        >>> for el in s.__iter__().getElementsByClass('Rest'):
        ...     print(el)
        <music21.note.Rest rest>
                
        
        ActiveSite is restored...
        
        >>> s2 = stream.Stream(id="s2")
        >>> s2.insert(0, r)
        >>> r.activeSite.id
        's2'

        >>> for el in s.__iter__().getElementsByClass('Rest'):
        ...     print(el.activeSite.id)
        s1   
        
        
        Classes work in addition to strings...
        
        >>> for el in s.__iter__().getElementsByClass(note.Rest):
        ...     print(el)
        <music21.note.Rest rest>
        
        '''
        # much faster in the most common case than calling common.isListLike
        self.addFilter(filter.ClassFilter(classFilterList))
        return self


    def getElementsByOffset(self, offsetStart, offsetEnd=None,
                    includeEndBoundary=True, mustFinishInSpan=False,
                    mustBeginInSpan=True, includeElementsThatEndAtStart=True):
        '''
        Adds a filter keeping only Music21Objects that
        are found at a certain offset or within a certain
        offset time range (given the start and optional stop values).


        There are several attributes that govern how this range is
        determined:


        If `mustFinishInSpan` is True then an event that begins
        between offsetStart and offsetEnd but which ends after offsetEnd
        will not be included.  The default is False.


        For instance, a half note at offset 2.0 will be found in
        getElementsByOffset(1.5, 2.5) or getElementsByOffset(1.5, 2.5,
        mustFinishInSpan = False) but not by getElementsByOffset(1.5, 2.5,
        mustFinishInSpan = True).

        The `includeEndBoundary` option determines if an element
        begun just at the offsetEnd should be included.  For instance,
        the half note at offset 2.0 above would be found by
        getElementsByOffset(0, 2.0) or by getElementsByOffset(0, 2.0,
        includeEndBoundary = True) but not by getElementsByOffset(0, 2.0,
        includeEndBoundary = False).

        Setting includeEndBoundary to False at the same time as
        mustFinishInSpan is set to True is probably NOT what you want to do
        unless you want to find things like clefs at the end of the region
        to display as courtesy clefs.

        The `mustBeginInSpan` option determines whether notes or other
        objects that do not begin in the region but are still sounding
        at the beginning of the region are excluded.  The default is
        True -- that is, these notes will not be included.
        For instance the half note at offset 2.0 from above would not be found by
        getElementsByOffset(3.0, 3.5) or getElementsByOffset(3.0, 3.5,
        mustBeginInSpan = True) but it would be found by
        getElementsByOffset(3.0, 3.5, mustBeginInSpan = False)

        Setting includeElementsThatEndAtStart to False is useful for zeroLength
        searches that set mustBeginInSpan == False to not catch notes that were
        playing before the search but that end just before the end of the search type.
        See the code for allPlayingWhileSounding for a demonstration.

        This chart, and the examples below, demonstrate the various
        features of getElementsByOffset.  It is one of the most complex
        methods of music21 but also one of the most powerful, so it
        is worth learning at least the basics.

            .. image:: images/getElementsByOffset.*
                :width: 600




        >>> st1 = stream.Stream()
        >>> n0 = note.Note("C")
        >>> n0.duration.type = "half"
        >>> n0.offset = 0
        >>> st1.insert(n0)
        >>> n2 = note.Note("D")
        >>> n2.duration.type = "half"
        >>> n2.offset = 2
        >>> st1.insert(n2)
        >>> out1 = list(st1.__iter__().getElementsByOffset(2))
        >>> len(out1)
        1
        >>> out1[0].step
        'D'
        >>> out2 = list(st1.__iter__().getElementsByOffset(1, 3))
        >>> len(out2)
        1
        >>> out2[0].step
        'D'
        >>> out3 = list(st1.__iter__().getElementsByOffset(1, 3, mustFinishInSpan=True))
        >>> len(out3)
        0
        >>> out4 = list(st1.__iter__().getElementsByOffset(1, 2))
        >>> len(out4)
        1
        >>> out4[0].step
        'D'
        >>> out5 = list(st1.__iter__().getElementsByOffset(1, 2, includeEndBoundary=False))
        >>> len(out5)
        0
        >>> out6 = list(st1.__iter__().getElementsByOffset(1, 2, includeEndBoundary=False, mustBeginInSpan=False))
        >>> len(out6)
        1
        >>> out6[0].step
        'C'
        >>> out7 = list(st1.__iter__().getElementsByOffset(1, 3, mustBeginInSpan=False))
        >>> len(out7)
        2
        >>> [el.step for el in out7]
        ['C', 'D']
        
        
        Note, that elements that end at the start offset are included if mustBeginInSpan is False
        
        >>> out8 = list(st1.__iter__().getElementsByOffset(2, 4, mustBeginInSpan=False))
        >>> len(out8)
        2
        >>> [el.step for el in out8]
        ['C', 'D']

        To change this behavior set includeElementsThatEndAtStart=False

        >>> out9 = list(st1.__iter__().getElementsByOffset(2, 4, mustBeginInSpan=False, includeElementsThatEndAtStart=False))
        >>> len(out9)
        1
        >>> [el.step for el in out9]
        ['D']



        >>> a = stream.Stream()
        >>> n = note.Note('G')
        >>> n.quarterLength = .5
        >>> a.repeatInsert(n, list(range(8)))
        >>> b = stream.Stream()
        >>> b.repeatInsert(a, [0, 3, 6])
        >>> c = list(b.__iter__().getElementsByOffset(2, 6.9))
        >>> len(c)
        2
        >>> c = list(b.recurse().getElementsByOffset(2, 6.9))
        >>> len(c)
        10


        Testing multiple zero-length elements with mustBeginInSpan:

        >>> c = clef.TrebleClef()
        >>> ts = meter.TimeSignature('4/4')
        >>> ks = key.KeySignature(2)
        >>> s = stream.Stream()
        >>> s.insert(0.0, c)
        >>> s.insert(0.0, ts)
        >>> s.insert(0.0, ks)
        >>> len(s.getElementsByOffset(0.0, mustBeginInSpan=True))
        3
        >>> len(s.getElementsByOffset(0.0, mustBeginInSpan=False))
        3

        OMIT_FROM_DOCS
        
        Same test as above, but with floats
        
        >>> out1 = st1.getElementsByOffset(2.0)
        >>> len(out1)
        1
        >>> out1[0].step
        'D'
        >>> out2 = st1.getElementsByOffset(1.0, 3.0)
        >>> len(out2)
        1
        >>> out2[0].step
        'D'
        >>> out3 = st1.getElementsByOffset(1.0, 3.0, mustFinishInSpan = True)
        >>> len(out3)
        0
        >>> out3b = st1.getElementsByOffset(0.0, 3.001, mustFinishInSpan = True)
        >>> len(out3b)
        1
        >>> out3b[0].step
        'C'
        >>> out3b = st1.getElementsByOffset(1.0, 3.001, mustFinishInSpan = True, mustBeginInSpan=False)
        >>> len(out3b)
        1
        >>> out3b[0].step
        'C'


        >>> out4 = st1.getElementsByOffset(1.0, 2.0)
        >>> len(out4)
        1
        >>> out4[0].step
        'D'
        >>> out5 = st1.getElementsByOffset(1.0, 2.0, includeEndBoundary = False)
        >>> len(out5)
        0
        >>> out6 = st1.getElementsByOffset(1.0, 2.0, includeEndBoundary = False, mustBeginInSpan = False)
        >>> len(out6)
        1
        >>> out6[0].step
        'C'
        >>> out7 = st1.getElementsByOffset(1.0, 3.0, mustBeginInSpan = False)
        >>> len(out7)
        2
        >>> [el.step for el in out7]
        ['C', 'D']

        :rtype: StreamIterator
        '''        
        self.addFilter(filter.OffsetFilter(offsetStart, offsetEnd, includeEndBoundary,
                                           mustFinishInSpan, mustBeginInSpan,
                                           includeElementsThatEndAtStart))
        return self
    
    #-------------------------------------------------------------
    
    @property
    def notes(self):
        '''
        >>> s = stream.Stream()
        >>> s.append(note.Note('C'))
        >>> s.append(note.Rest())
        >>> s.append(note.Note('D'))
        >>> for el in s.__iter__().notes:
        ...     print(el)
        <music21.note.Note C>
        <music21.note.Note D>
        '''
        self.addFilter(filter.ClassFilter('NotRest'))
        return self

    @property
    def notesAndRests(self):
        '''
        >>> s = stream.Stream()
        >>> s.append(meter.TimeSignature('4/4'))
        >>> s.append(note.Note('C'))
        >>> s.append(note.Rest())
        >>> s.append(note.Note('D'))
        >>> for el in s.__iter__().notesAndRests:
        ...     print(el)
        <music21.note.Note C>
        <music21.note.Rest rest>
        <music21.note.Note D>
        
        
        chained filters... (this makes no sense since notes is a subset of notesAndRests
        
        
        >>> for el in s.__iter__().notesAndRests.notes:
        ...     print(el)
        <music21.note.Note C>
        <music21.note.Note D>        
        '''
        self.addFilter(filter.ClassFilter('GeneralNote'))
        return self


#------------------------------------------------------------------------------
class RecursiveIterator(StreamIterator):
    '''
    >>> b = corpus.parse('bwv66.6')
    >>> ri = stream.iterator.RecursiveIterator(b, streamsOnly=True)
    >>> for x in ri:
    ...     print(x)
    <music21.stream.Part Soprano>
    <music21.stream.Measure 0 offset=0.0>
    <music21.stream.Measure 1 offset=1.0>
    <music21.stream.Measure 2 offset=5.0>
    ...
    <music21.stream.Part Alto>
    <music21.stream.Measure 0 offset=0.0>
    ...
    <music21.stream.Part Tenor>
    ...
    <music21.stream.Part Bass>
    ...
    
    >>> hasExpressions = lambda el, i: True if (hasattr(el, 'expressions') and len(el.expressions) > 0) else False
    >>> for el in stream.iterator.RecursiveIterator(b).addFilter(hasExpressions):
    ...     printer = (el, el.expressions)
    ...     print(printer)
    (<music21.note.Note C#>, [<music21.expressions.Fermata>])
    (<music21.note.Note A>, [<music21.expressions.Fermata>])
    (<music21.note.Note F#>, [<music21.expressions.Fermata>])
    (<music21.note.Note C#>, [<music21.expressions.Fermata>])
    (<music21.note.Note G#>, [<music21.expressions.Fermata>])
    (<music21.note.Note F#>, [<music21.expressions.Fermata>])
    '''
    def __init__(self, srcStream, filters=None, restoreActiveSites=True, 
                        streamsOnly=False, includeSelf=False):
        super(RecursiveIterator, self).__init__(srcStream, filters, restoreActiveSites)
        self.includeSelf = includeSelf
        if streamsOnly is True:
            self.filters.append(filter.ClassFilter('Stream'))
        self.recursiveIterator = None
        
    def __next__(self):
        if self.recursiveIterator is not None:
            try:
                return self.recursiveIterator.next()
            except StopIteration:
                self.recursiveIterator = None
                
        if self.index == 0 and self.includeSelf is True and self.matchesFilters(self.srcStream):
            self.includeSelf = False
            return self.srcStream

        if self.index >= self.streamLength:
            self.cleanup()
            raise StopIteration
        
        self.index += 1 # increment early in case of an error.
    
        try:
            e = self.srcStreamElements[self.index - 1]
        except IndexError:
            # this may happen in the number of elements has changed
            return self.__next__()

        if e.isStream:
            self.recursiveIterator = RecursiveIterator(srcStream=e,
                                           restoreActiveSites=self.restoreActiveSites,
                                           filters=self.filters, # shared list...
                                           includeSelf=False, # always for inner streams
                                           )
        if self.matchesFilters(e) is False:
            return self.__next__()            
        
        if self.restoreActiveSites is True:
            e.activeSite = self.srcStream

        return e

    next = __next__



class Test(unittest.TestCase):
    pass

if __name__ == '__main__':
    import music21
    music21.mainTest(Test)
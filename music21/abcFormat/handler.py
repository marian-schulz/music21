from music21.abcFormat.tokens import *

from typing import *
from collections import defaultdict

# @TODO import from tokens
DEFAULT_SYMBOLS = {}


# Group of tokens in a Measure
ABCMeasure = List[ABCToken]

class ABCBaseHandler():
    def __init__(self, src: Union[List[ABCToken], str], abcVersion: ABCVersion=None,
                 lineBreaksDefinePhrases: bool=False):
        """
        Baseclass of all classes to handle ABCTokens
        Any subclass hast to implement the process() method.

        Arguments:
            tokens:
                Setup the Handler for this ABCtoken listr
            abcVersion:
                Version of the ABC Format
            lineBreaksDefinePhrases:
                Not implemented feature for phrases on linebreaks
        """

        # If the ABC version is set explicit, the version string in the ABC text is ignored.

        self.abcVersion = abcVersion
        self.lineBreaksDefinePhrases = lineBreaksDefinePhrases

        if isinstance(data, str):
            if abcVersion is None:
                self.abcVersion = parseABCVersion(src)
            self._data = abcTokenizer(src, abcVersion=self.abcVersion)
        else:
            self._data = src

    def hasNotes(self) -> bool:
        '''
        Check if the Handler holds any Chords or Notes

        >>> t = abcTokenizer('M:6/8\\nL:1/8\\nK:G\\n')
        >>> ah1 = abcFormat.ABCHandler(t)
        >>> ah1.hasNotes()
        False

        >>> abcStr = abcTokenizer('M:6/8\\nL:1/8\\nK:G\\nc1D2')
        >>> ah2 = abcFormat.ABCHandler(t)
        >>> ah2.hasNotes()
        True
        '''
        if not self._data:
            return False

        return any(isinstance(t, (ABCGeneralNote)) for t in self)

    def process(self):
        return NotImplemented

    @property
    def tokens(self) -> List[ABCToken]:
        return list(self)

    def __add__(self, other):
        '''
        Return a new handler adding the tokens in both

        Contrived example appending two separate keys.

        Used in polyphonic metadata merge
        '''
        ah = self.__class__()
        ah._data = self.tokens + other.tokens()
        return ah

    def __len__(self):
        return len(self.tokens)

    def __iter__(self):
        return NotImplemented

    @tokens.setter
    def tokens(self, tokens: ABCToken):
        raise NotADirectoryError()


class ABCHandler(ABCBaseHandler):
    '''
    An ABCHandler is able to divide elements of a character stream into objects and handle
    store in a list, and passes global information to components

    Optionally, specify the (major, minor, patch) version of ABC to process--
    e.g., (1.2.0). If not set, default ABC 1.3 parsing is performed.

    New in v6.2 -- lineBreaksDefinePhrases -- does not yet do anything
    If lineBreaksDefinePhrases is True then new lines within music elements
    define new phrases. This is useful for parsing extra information from t
    he Essen Folksong repertory (@TODO)
    '''

    def __init__(self, tokens: List[ABCToken], parent: Optional[ABCHeaderMixin]=None,
                 abcVersion: ABCVersion=None, lineBreaksDefinePhrases: bool=False):
        """
        Arguments:
            tokens:
                Setup the Handler for this ABCtoken list
            parent:
                If this handler is a subhandler, set the parent ABC handler
            abcVersion:
                Version of the ABC Format
            lineBreaksDefinePhrases:
                Not implemented feature for phrases on linebreaks
        """
        super().__init__(tokens, abcVersion, lineBreaksDefinePhrases)
        # If the ABC version is set explicit, the version string in the ABC text is ignored.

        self.header = header
        self.timeSignature : Optional[meter.TimeSignature] = None
        self.keySignature : Optional[key.KeySignature] = None
        self.abcDirectives : Optional[Dict[str,str]] = None

        # the default length (L:) of an note/chord without length modifier (1.0 = quarter note)
        self.unitNoteLength : Optional[float] = None

        # Dictonary for redefined symbols (U:)
        self.userDefined : Optional[Dict[str, ABCToken]] = None

        # title, composer, orgin..
        self.metadata : Optional[Dict[str, str]] = None

    def process(self):
        self._process_header()
        self.tokens = list(self._process_tokens())

    def get_bars(self) -> List[ABCBarHandler]:
        """
        Get a list of ABCBarHandler from the tokens of this Handler.
        It is recommended to process the tokens of this handler first.

        Returns:
            List of ABCBarHandlers
        Raises:
            ABCHandler dxception if not any ABCBar tokens are found
        """
        measures = []
        active_measure = []
        lastBarToken = None
        for token in self.tokens:
            if isinstance(ABCBar, token):
                if active_measure:
                    measures.append(
                        ABCBarHandler(active_measure, leftBar=lastBarToken, rightBar=token)
                    )
                    active_measure = []
                else:
                    # No Tokens in the active measure
                    pass
                lastBarToken = token
            else:
                active_measure.append(token)


        if not measures:
            raise ABCHandlerException('The tokens of this handler do not define any bars')

        if active_measure:
            # FOr the rest, autofinish the last bar
            measures.append(
                ABCBarHandler(active_measure, leftBar=lastBarToken)
            )
        return measures

    @property
    def tokens(self) -> List[ABCToken]:
        return self._data

    @tokens.setter
    def tokens(self, tokens: ABCToken):
        self._data = tokens

    def _process_chord_tokens(self, chord: ABCChord) -> Iterator[ABCNote]:
        """
        Process the intern context of a chord.
        Because of the vertical nature of the note in a chord,
        the process() method is not well suited.

        @TODO: Howto appply accidental proagation to chord notes ?
        Argument:
            ABC Chord
        Returns:
            Generator for processed subtokens
        """

        # The chord notes can have individeull Expressions and Articulations
        lastExpressions: List[ABCExpression] = []
        lastArticulations: List[ABCArticulation] = []

        for token in chord.subTokens:
            # Lookup a redefinable symbol and continue the processing
            if isinstance(token, ABCSymbol):
                try:
                    token = ABCSymbol.lookup(self.userDefined)
                except KeyError:
                    environLocal.printDebug(['ABCSymbol "{self.src}" without definition found.'])
                    continue

            if isinstance(token, ABCArticulation):
                lastArticulations.append(token)
            elif isinstance(token, ABCExpression):
                lastExpressions.append(token)
            elif isinstance(token, ABCNote) and not token.isRest:
                # Attached the collected articulations to notes & chords
                token.articulations = lastArticulations
                lastArticulations = []
                # Attached the collected expressions to to notes & chords
                token.expressions = lastExpressions
                lastExpressions = []
                yield token

    def _process_tokens(self) -> Iterator[ABCTokens]:
        """
        Process the token list.
        Establish the context between the tokens, and track states.

        Remove tokens after it has become a property of a note or chord.
        Properties of a note/chord are ABCGrace, ABCExpression, ABCArticulation
        Remove ABCField tokens that are not represented in the music21 stream after
        their evaluation.
        Remove the ABCParenStop token after the enclosed tokens has been captured.
        Group tokens to ABCMeasures.
        """
        lastNote: Optional[ABCGeneralNote] = None
        lastTuplet: Optional[ABCTuplet] = None
        lastTie: Optional[ABCTie] = None
        lastGrace: Optional[ABCGraceStart] = None
        lastBrokenRythm: Optional[ABCBrokenRhythm] = None
        lastExpressions: List[ABCExpression] = []
        lastArticulations: List[ABCArticulation] = []

        activeParens = []
        activeSpanners = []
        accidentalized = {}

        for token in self:

            # Lookup a redefinable symbol and continue the processing
            if isinstance(token, ABCSymbol):
                try:
                    # there are maybe more than one token in the definition
                    tokens = ABCSymbol.lookup(self.userDefined)
                except KeyError:
                    environLocal.printDebug(['ABCSymbol "{self.src}" without definition found.'])
                    continue

            # process note & chords second, they are the most common tokens
            if isinstance(token, ABCGeneralNote):

                token.activeDefaultQuarterLength = self.unitNoteLength
                token.activeKeySignature = self.keySignature
                token.applicableSpanners = self.activeSpanners[:]

                # Attached the collected articulations to notes & chords
                token.articulations = lastArticulations
                lastArticulations = []

                # Attached the collected expressions to to notes & chords
                token.expressions = lastExpressions
                lastExpressions = []

                if lastTie is not None:
                    token.tie = 'stop'
                    lastTie = None

                if lastBrokenRythm:
                    lastBrokenRythm.set_notes(lastNote, token)
                    lastBrokenRythm = None

                if lastGrace is not None:
                    token.inGrace = True
                if lastTuplet is None:
                    pass
                elif lastTuplet.noteCount == 0:
                    lastTuplet = None
                else:
                    lastTuplet.noteCount -= 1  # decrement
                    # add a reference to the note
                    token.activeTuplet = lastTuplet.m21Object()

                # @TODO: I dont like that, can we trigger this state with changing diretctives ?
                self.accidentalPropagation = self._accidentalPropagation()

                if isinstance(token, ABCChord):
                    # Process the inner structure of a Chord
                    token.subTokens = self._process_chord_tokens(token)

                elif isinstance(token, ABCNote) and not token.isRest:
                    # @TODO: is accidental propagation relevant for the chord subnotes ?
                    if token.accidental:
                        # Remember the accidental of this note
                        if self.propagation == 'octave':
                            accidentalized[(token.pitch_name, token.octave)] = token.accidental
                        elif self.propagation == 'pitch':
                            accidentalized[token.pitch_name] = token.accidental
                    else:
                        # Lookup the active accidentals
                        if self.propagation == 'pitch' and token.pitch_name in accidentalized:
                            token.carriedAccidental = accidentalized[token.pitch_name]
                        elif self.propagation == 'octave' and (token.pitch_name, token.octave) in accidentalized:
                            token.carriedAccidental = accidentalized[(token.pitch_name, token.octave)]

                # remember this note/chord
                lastNote = token

            elif isinstance(token, ABCField):
                if token.isMeter():
                    ts = token.getTimeSignatureObject()
                    if ts:
                        self.timeSignature = ts

                    elif token.isUserDefinedSymbol():
                        try:
                            key, value = token.getUserDefinedSymbol()
                            self.userDefined[key] = value
                        except AttributeError:
                            environLocal.debug(f'Invalid synatx in for userdefined symbol: [{token}]')
                    # this token is no longer needed
                elif token.isDefaultNoteLength():
                    self.unitNoteLength = token.getDefaultQuarterLength()
                    # this token is no longer needed
                    continue
                elif token.isKey():
                    self.keySignature = token.getKeySignatureObject()
                elif token.isTempo():
                    tempo = token.getMetronomeMarkObject()

            elif lastNote and isinstance(token, ABCBrokenRhythm):
                lastBrokenRythm = token
                # this token is no longer needed
                continue

            elif isinstance(token, ABCTuplet):
                token.updateRatio(self.timeSignature)
                token.updateNoteCount()
                lastTuplet = token
                activeParens.append('Tuplet')

            elif isinstance(token, ABCSpanner):
                activeSpanners.append(token.m21Object())
                activeParens.append(token)

            elif isinstance(token, ABCParenStop):
                if activeParens:
                    p = activeParens.pop()
                    if isinstance(p, ABCSpanner):
                        activeSpanners.pop()
                # the Stop Token is no longer needed
            elif isinstance(token, ABCTie):
                if lastNote and lastNote.tie == 'stop':
                    lastNote.tie = 'continue'
                elif lastNote:
                    lastNote.tie = 'start'
                lastTie = token
                # the tie token is not longer needed
                continue
            elif isinstance(token, ABCGraceStart):
                lastGrace = token
                # The GraceStop token is not longer needed
                continue
            elif isinstance(token, ABCGraceStop):
                lastGrace = None
                # The GraceStart token is not longer needed
                continue
            elif isinstance(token, ABCArticulation):
                lastArticulations.append(token)
                # this token is no longer needed
            elif isinstance(token, ABCExpression):
                lastExpressions.append(token)
                # this token is no longer needed
            elif isinstance(token, ABCBar):
                self.accidentalized = {}

            yield token

    def _process_header(self):
        """
        Apply default values from the parent if available and parse the header tokens.
        Before the header is parsed, the header of the parent should be parsed, if available.
        """
        # Get default values from the parent Handler
        if self.parent is not None:
            self.unitNoteLength = parent.unitNoteLength
            self.timeSignature = parent.timeSignature
            self.userDefined = dict(parent.userDefined)
            self.keySignature = parent.keySignature
            self.abcDirectives = dict(header.abcDirectives)

        if self.header is not None:
            # Only instances with an header (event they are empty)
            # got metadatas from the parent handler
            if self.parent is not None:
                if self.parent.metadata is None:
                    # If an instance has metadata, the parent should too
                    environLocal.printDebug('Parent of ABCHeaderMixin has no metadata.')
                    self.metadata = {}
                else:
                    # Copy the metadata from the parent
                    self.metadata = Dict(self.parent.metadata)

            # Parse the own header
            for token in self.header:
                if isinstance(token, ABCDirective):
                    self.abcDirectives[token.key] = token.value
                elif isinstance(token, ABCField):
                    if token.isMeter():
                        ts = token.getTimeSignatureObject()
                        if ts:
                            self.timeSignature = ts
                            if self.unitNoteLength is None:
                                self.unitNoteLength = token.getDefaultQuarterLength()
                    elif token.isUserDefinedSymbol():
                        try:
                            key, value = token.getUserDefinedSymbol()
                            self.userDefined[key] = value
                        except AttributeError:
                            environLocal.debug(f'Invalid synatx in for userdefined symbol: [{token}]')
                    elif token.isDefaultNoteLength():
                        self.defaultNoteLength = token.getDefaultQuarterLength()
                    elif token.isKey():
                        self.keySignature = token.getKeySignatureObject()
                    elif token.isComposer():
                        self.metadata['composer'] = token.data
                    elif token.isOrigin():
                        self.metadata['origin'] = token.data
                    elif token.isTempo():
                        self.tempo = token.getMetronomeMarkObject()
                    elif token.isTitle():
                        self.metadata['title'] = token.data

            # The default unitNoteLength is 0.5 ( eight note )
            if self.unitNoteLength is None:
                self.unitNoteLength = 0.5

    def __len__(self):
        return len(self.tokens)

    def __iter__(self):
        yield from self.header
        yield from self._data

    def hasNotes(self) -> bool:
        '''
        Check if the Handler holds any Chords or Notes

        >>> t = abcTokenizer('M:6/8\\nL:1/8\\nK:G\\n')
        >>> ah1 = abcFormat.ABCHandler(t)
        >>> ah1.hasNotes()
        False

        >>> abcStr = abcTokenizer('M:6/8\\nL:1/8\\nK:G\\nc1D2')
        >>> ah2 = abcFormat.ABCHandler(t)
        >>> ah2.hasNotes()
        True
        '''
        if not self._data:
            return False

        return any(isinstance(t, (ABCGeneralNote)) for t in self)

    def process(self):
        return NotImplemented

    @property
    def tokens(self) -> List[ABCToken]:
        return self.header.tokens + [item for voice in self.voices for item in voice]

    @tokens.setter
    def tokens(self, tokens) -> List[ABCToken]:


    def __add__(self, other):
        '''
        Return a new handler adding the tokens in both

        Contrived example appending two separate keys.

        Used in polyphonic metadata merge
        '''
        ah = self.__class__()
        ah._data = self._data + other.tokens()
        return ah

    def __len__(self):
        return len(self.tokens)

    def __iter__(self):
        return NotImplemented


class ABCBarHandler(ABCHandler):
    '''
    A Handler specialized for storing bars. All left
    and right bars are collected and assigned to attributes.
    '''

    def __init__(self, tokens, leftBar, rightBar: Optional[ABCBar] = None):
        # tokens are ABC objects in a linear stream
        super().__init__(tokens)
        self.leftBar =  leftBar
        self.rightBar = rightBar

    @property
    def tokens(self) -> List[ABCToken]:
        return self._data

    def __iter__(self):
        return NotImplemented


class ABCTune():
    '''
    Handles all tokens belonging to an ABCTune

    An ABCTuneHandler maintains a token lists and a dictonary for ABCVoice Handlers.

    '''
    def __init__(self, tokens: List[ABCToken],
                       abcVersion=None,
                       lineBreaksDefinePhrases=False):

        header, voices = self._split_header_and_voices(tokens)
        super().__init__((header, voices), abcVersion, lineBreaksDefinePhrases)

        self.header: ABCHeader = ABCHeader(tokens=header, abcVersion=self.abcVersion)
        self.voices: List[ABCBarHandler] = [
            ABCHandler(v, abcVersion, lineBreaksDefinePhrases) for v in voices]


    def __add__(self, other):
        '''
        Return a new handler adding the tokens in both

        Contrived example appending two separate keys.

        Used in polyphonic metadata merge
        '''
        ah = self.__class__()
        ah._data = self.tokens + other.tokens()
        return ah


    def __len__(self):
        return len(self.header) + sum(len(voice) for voice in self.voices)


    def __iter__(self):
        yield from self.header
        for voice in self.voices:
            yield from voice


    @tokens.setter
    def tokens(self, tokens: ABCToken):
        self.header, self.voices = ABCTune._split_header_and_voices(tokens)
        self._data = (self.header, self.voices)

    @classmethod
    def _split_header_and_voices(cls, tokens: List[ABCToken]) -> Tuple[List[ABCToken], List[List[ABCToken]]]:
        active_voice = []
        all_voices = []
        header : List[ABCToken] = []
        voices = {}
        voices['1'] = active_voice
        tokenIter = iter(tokens)

        for token in tokenIter:
            if isinstance(token, ABCField):
                if token.tag in "ABCDFGHILMmNOPQRSTrSUWXZ":
                    header.append(token)
                elif token.isKey():
                    # Stop, regular end of the tune header
                    header.append(token)
                    break
                elif token.isVoice():
                    # Voice field in the header
                    voice_id = token.getVoiceId()
                    if voice_id:
                        if voice_id == '*':
                            # this is for all voices
                            all_voices.append(token)
                            for v in self.voices.values():
                                v.append(token)
                            continue
                        elif voice_id in voices:
                            # change active voice
                            active_voice = voices[voice_id]
                        else:
                            # create new voice, start with the tokens for all voices
                            active_voice = all_voices[:]
                            voices[voice_id] = active_voice

                        active_voice.append(token)
                elif token.tag in "sw":
                    # This is a body Metatag
                    # We continue with the Body
                    active_voice.append(token)
                    break
                else:
                    # Skip unknown Token
                    environLocal.printDebug([f'Skip unknown header field tag "{token.tag}" in tune header.'])
                    continue
            elif isinstance(token, ABCDirective):
                header.append(token)
            else:
                # This is not an ABC field or directive
                # We continue with the Body
                active_voice.append(token)
                break

        # Continue with the tune body
        for token in tokenIter:
            if isinstance(token, ABCField):
                if token.isVoice():
                    voice_id = token.getVoiceId()
                    if voice_id:
                        if voice_id == '*':
                            # no all voice notation in the body
                            continue
                        if voice_id in voices:
                            active_voice_tokens = voices[voice_id]
                        else:
                            active_voice_tokens = []
                            voices[voice_id] = active_voice_tokens

            active_voice.append(token)

        return header, voices.values()



    def process(self, tune_book: Optional['ABCTuneBook']=None):
        # process header data
        self.header.process(parent=tune_book.header)

        # process the voices
        for voice in self.voices:
            voice.process(header=self.header)

    def __len__(self):
        def __len__(self):
            return len(self.header) + sum(len(v) for v in self.voices)

        def __iter__(self):
            yield from self.header
            for voice in self.voices:
                yield from self.voice

    def __str__(self):
        o = ['']
        if self.header:
            o.extend(f'{t}' for t in self.header)

        for t in self.voices:
            o.append(str(t))

        return "\n".join(o)

class ABCTuneBook(ABCHandler):
    '''
    Handles all tokens belonging to an ABC FileHeader

    An ABCFile maintains a token list for the FileHeader
    and an Dictonary for ABCTunes of the ABCFile.
    '''
    def __init__(self, tokens: List[ABCToken], abcVersion=None, lineBreaksDefinePhrases=False):
        '''
        Any metadata token until the first referencenumber belongig to the FileHeader.

        Fallbacks:
        If there is a non metadata field before the first reference number or an metadataf ield not allowed
        in an abcFileheader all the collected Tokens belong to an implizit Tune with 'X:1' as
        reference number. If later an X: Field with the same Refrencenumber appears did we (recursively)
        increase the Refrencenumbers until we have no conflicts.
        '''
        super().__init__(abcVersion, lineBreaksDefinePhrases)
        self.header : ABCHeader = []
        self.tunes : Dict[int, ABCTunes] = {}

        # token list for the header
        header : List[ABCToken] = []
        tokenIter = iter(tokens)

        active_reference_number = None
        for token in tokenIter:
            if isinstance(token, ABCField):
                if token.tag in "ABCDFGHILMmNORSrSUZ":
                    header.append(token)
                    continue
            break
        else:
            # This abc tune book contains only Metadata, maybe an abc header include file
            self.header = ABCHeader(header, abcVersion, lineBreaksDefinePhrases)
            return

        if header:
            # Create the ABC header handler of this tune book
            self.header = ABCHeader(header, abcVersion, lineBreaksDefinePhrases)

        if isinstance(token, ABCField) and token.isReferenceNumber():
            active_reference_number = self.find_next_refnum(token.data)
            token.data = active_reference_number
            active_tune = [token]
        else:
            # This is not a regular abc tune book.
            # create a ref number meta data token for the tune and add the last token
            active_reference_number = self.find_next_refnum()
            active_tune = [ABCField(f'X:{active_reference_number}'), token]

        # Sort all other tokens to the active tunes
        for token in tokenIter:
            if isinstance(token, ABCField):
                if token.tag == 'X':
                    # create a tune with all the collected tockens
                    self.tunes[active_reference_number] = ABCTune(active_tune,
                                                                  abcVersion,
                                                                  lineBreaksDefinePhrases)

                    active_reference_number = self.find_next_refnum(token.data)
                    token.data = active_reference_number
                    active_tune = [token]
                    continue

            active_tune.append(token)

        self.tunes[active_reference_number] = ABCTune(active_tune,
                                                      abcVersion,
                                                      lineBreaksDefinePhrases)

    def process(self):
        self.header.process()
        for tune in self.tunes.values():
            tune.process(header=self.header)

    def find_next_refnum(self, numstr: Optional[str]=None) -> int:
        l = len(self.tunes)
        if numstr is None:
            # No number
            num = l
        else:
            numstr = ''.join(i for i in numstr if i.isdigit())
            if not numstr:
                # No number
                num = l
            else:
                num = int(numstr)

        # if the number is not already in use
        if num not in self.tunes:
            return num

        # start with until we found a free number
        return next(n for n in range(l, 2 * l + 1) if n not in self.tunes.keys())

    def __len__(self):
        return sum(len(t) for t in self.tunes.values()) + len(self.header)

    def __iter__(self):
        yield from self.header

        for tune in self.tunes:
            yield from tune

    def __str__(self):
        if self.header:
            o = [ f'{t}' for t in self.header]
            o+= '\n'
        else:
            o = []

        o.extend(str(t) for t in self.tunes.values() )
        return "\n".join(o)

def translate(src: str):
    abcVersion = parseABCVersion(src)
    tokens = abcTokenizer(src, abcVersion)
    tune_book = ABCTuneBook(tokens)
    return tune_book

if __name__ == '__main__':
    from music21.abcFormat import testFiles
    print(translate(testFiles.aleIsDear))

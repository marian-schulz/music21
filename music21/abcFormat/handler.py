from music21.abcFormat.tokens import *

from typing import *
from collections import defaultdict

# @TODO import from tokens
DEFAULT_SYMBOLS = {}


class ABCHandler:
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

    def __init__(self, abcVersion=None, lineBreaksDefinePhrases=False):

        # If the ABC version is set explicit, the version string in the ABC text is ignored.
        self.abcVersion = abcVersion
        self.lineBreaksDefinePhrases = lineBreaksDefinePhrases

    def process(self, tokens: Iterable[ABCToken]):
        return NotImplemented

    def __len__(self):
        return NotImplemented

    def __iter__(self):
        return NotImplemented

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
        if not self.tokens:
            return False
        return any(isinstance(t, (ABCGeneralNote)) for t in self.tokens)

class ABCFlatTokenHandler(ABCHandler):
    def __init__(self, tokens: List[ABCToken],
                 abcVersion=None,
                 lineBreaksDefinePhrases=False):
        super().__init__(abcVersion, lineBreaksDefinePhrases)
        self.tokens = tokens

    def __add__(self, other):
        '''
        Return a new handler adding the tokens in both

        Contrived example appending two separate keys.

        Used in polyphonic metadata merge
        '''
        ah = self.__class__()
        ah.tokens = self.tokens + other.tokens
        return ah

    def __len__(self):
        return len(self.tokens)

    def __iter__(self):
        if self.tokens is None:
            return
        return iter(self.tokens)

    def __getitem__(self, index):
        return self.token[index]


class ABCHeader(ABCFlatTokenHandler):

    def __init__(self, tokens: List[ABCToken],
                 abcVersion=None,
                 lineBreaksDefinePhrases=False):
        super().__init__(tokens, abcVersion, lineBreaksDefinePhrases)
        self.defaultNoteLength = None
        self.timeSignature = None
        self.keySignature = None
        self.composer = None
        self.origin = None
        self.title = None
        self.tempo = None
        self.title = None
        self.book = None
        self.abcDirectives : Dict = {}
        self.userDefined : Dict = {}

    def process(self, parent: Optional['ABCHeader'] = None):

        if parent:
            self.unitNoteLength = parent.unitNoteLength
            self.timeSignature = parent.timeSignature
            self.userDefined = dict(parent.userDefined)
            self.composer = parent.composer
            self.origin = parent.origin
            self.tempo = parent.tempo
            self.title = parent.title
            self.book = parent.book
            self.abcDirectives = self.abcDirectives

        for token in self.tokens:
            if token.isMeter():
                ts = token.getTimeSignatureObject()
                if ts:
                    self.timeSignature = ts
                    if self.unitNoteLength is None:
                        self.unitNoteLength = token.getDefaultQuarterLength()
            elif token.isUserDefinedSymbol():
                key, value = token.getUserDefinedSymbol()
                self.userDefined[key] = value
            elif token.isDefaultNoteLength():
                self.defaultNoteLength = token.getDefaultQuarterLength()
            elif token.isKey():
                self.keySignature = token.getKeySignatureObject()
            elif token.isComposer():
                self.composer = token.data
            elif token.isOrigin():
                self.origin = token.data
            elif token.isTempo():
                self.tempo = token.getMetronomeMarkObject()
            elif token.isTitle():
                self.title = token.data

        if self.unitNoteLength is None:
            self.unitNoteLength = 0.5


class ABCVoice(ABCFlatTokenHandler):
    '''
    Handles all tokens belonging to an ABCVoice
    '''
    def __init__(self, tokens: List[ABCToken],
                 abcVersion=None,
                 lineBreaksDefinePhrases=False):

        super().__init__(tokens, abcVersion, lineBreaksDefinePhrases)

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

    def process(self, header: Optional[ABCHeader]=None):
        # Init vars from tune header
        if tune:
            unitNoteLength = header.unitNoteLength
            timeSignature = header.timeSignature
            userDefined = dict(header.userDefined if header.userDefined else DEFAULT_SYMBOLS)
            keySignature = header.keySignature
        else:
            unitNoteLength = 0.5
            timeSignature = None
            userDefined = {}
            keySignature = None

        lastNote: Optional[ABCGeneralNote] = None
        lastTuplet : Optional[ABCTuplet] = None
        lastTie: Optional[ABCTie] = None
        lastGrace: Optional[ABCGraceStart] = None
        lastBrokenRythm : Optional[ABCBrokenRhythm] = None
        lastExpressions : List[ABCExpression] = []
        lastArticulations : List[ABCArticulation] = []
        activeParens = []
        activeSpanners = []
        accidentalized = {}

        for token in self:
            # note & chords first, they are the most common tokens
            if isinstance(token, ABCGeneralNote):
                if isinstance(token, ABCChord):
                    # process the inner chord subtokens
                    token.chordHandler.process(token.subTokens)

                elif isinstance(token, ABCNote) and not token.isRest:
                    # @TODO: is accidental propagation relevant for the chord subnotes ?
                    propagation = self._accidentalPropagation()

                    if token.accidental:
                        # Remember the accidental of this note
                        if propagation == 'octave':
                            accidentalized[(token.pitch_name, token.octave)] = token.accidental
                        elif propagation == 'pitch':
                            accidentalized[token.pitch_name] = token.accidental
                    else:
                        # Lookup the active accidentals
                        if propagation == 'pitch' and token.pitch_name in accidentalized:
                            token.carriedAccidental = accidentalized[token.pitch_name]
                        elif propagation == 'octave' and (token.pitch_name, token.octave) in accidentalized:
                            token.carriedAccidental = accidentalized[(token.pitch_name, token.octave)]

                token.activeDefaultQuarterLength = unitNoteLength
                token.activeKeySignature = keySignature
                token.applicableSpanners = self.activeSpanners[:]  # fast copy of a list

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

                # remember this note/chord
                lastNote = token
                continue

            if isinstance(token, ABCMetadata):
                if token.isMeter():
                    ts = token.getTimeSignatureObject()
                    if ts:
                        timeSignature = ts
                elif token.isUserDefinedSymbol():
                    key, value = token.getUserDefinedSymbol()
                    userDefined[key] = value
                elif token.isDefaultNoteLength():
                    unitNoteLength = token.getDefaultQuarterLength()
                elif token.isKey():
                    keySignature = token.getKeySignatureObject()
                elif token.isTempo():
                    tempo = token.getMetronomeMarkObject()
                continue

            if isinstance(token, ABCBrokenRhythm):
                # we need a token to the left side for the broken rythm
                if lastNote:
                    lastBrokenRythm = token

            if isinstance(token, ABCUserDefinedSymbol):
                try:
                    token = userDefined[token.src]
                except KeyError:
                    # Symbol has no definition !
                    continue

            elif isinstance(token, ABCBar):
                # reset active accidentals on bar change
                accidentalized = {}

                # need to update tuplets with currently active meter
            elif isinstance(token, ABCTuplet):
                token.updateRatio(timeSignature)
                # set number of notes that will be altered
                # might need to do this with ql values, or look ahead to nxt
                # token
                token.updateNoteCount()
                lastTuplet = token
                activeParens.append('Tuplet')

            # notes within slur marks need to be added to the spanner
            elif isinstance(token, ABCSpanner):
                activeSpanners.append(token.m21Object())
                activeParens.append(token)

            elif isinstance(token, ABCParenStop):
                if self.activeParens:
                    p = self.activeParens.pop()
                    if isinstance(p, ABCSpanner):
                        self.activeSpanners.pop()

            elif isinstance(token, ABCTie):
                # @TODO: Question - can we lost an relevant 'lastNodeToken' ?
                if lastNote and lastNote.tie == 'stop':
                    lastNote.tie = 'continue'
                elif lastNote:
                    lastNote.tie = 'start'
                lastTie = token

            elif isinstance(token, ABCGraceStart):
                lastGrace = token
            elif isinstance(token, ABCGraceStop):
                lastGrace = None

    def __str__(self):
        return "\n".join(f'\t\t{t}' for t in self.tokens)

class ABCTune(ABCHandler):
    '''
    Handles all tokens belonging to an ABCTune

    An ABCTuneHandler maintains a token lists and a dictonary for ABCVoice Handlers.
    '''
    def __init__(self, tokens: List[ABCToken],
                       abcVersion=None,
                       lineBreaksDefinePhrases=False):
        super().__init__(abcVersion, lineBreaksDefinePhrases)

        active_voive = []
        all_voices = []
        header : List[ABCToken] = []
        voices = {}
        voices['1'] = active_voive
        tokenIter = iter(tokens)
        for token in tokenIter:
            if isinstance(token, ABCField):
                if token.tag in "ABCDFGHILMmNOPQRSTrSUWZ":
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
                            active_voive = voices[voice_id]
                        else:
                            # create new voice, start with the tokens for all voices
                            active_voive = all_voices[:]
                            voices[vid] = active_voive

                        active_voice.append(token)
                elif token.tag in "sw":
                    # This is a body Metatag
                    # We continue with the Body
                    active_voive.append(token)
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
                active_voive.append(token)
                break

        # Create the Header Handler
        self.header: ABCHeader = ABCHeader(tokens=header, abcVersion=self.abcVersion)

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
                            active_voive_tokens = voices[voice_id]
                        else:
                            active_voive_tokens = []
                            voices[voice_id] = active_voive_tokens

            active_voive.append(token)

        # Last Step, create Voice Handler for each voice
        self.voices : List[ABCVoice] = [ABCVoice(t, abcVersion, lineBreaksDefinePhrases)
                                       for t in voices.items()]

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
            o.extend(f'\t{t}' for t in self.header)

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
                    self.tunes[active_reference_number] = abcTune(active_tune,
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
        else:
            o = []

        o.extend( str(t) for t in self.tunes.values() )
        return "\n".join(o)

def translate(src: str):
    abcVersion = parseABCVersion(src)
    tokens = abcTokenizer(src, abcVersion)
    tune_book = ABCTuneBook(tokens)
    return tune_book

if __name__ == '__main__':
    from music21.abcFormat import testFiles
    print(translate(testFiles.aleIsDear))

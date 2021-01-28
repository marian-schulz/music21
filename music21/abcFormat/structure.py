from music21.abcFormat import *
from typing import *

# @TODO import from tokens
DEFAULT_SYMBOLS = {}

class abcVoice(ABCHandler):
    '''
    Handles all tokens belonging to an ABCVoice
    '''
    def __init__(self, tokens, tune: Optional['abcTune']):
        self.tokens = tokens
        self.tune = tune

    def process(self):
        pass

    def __len__(self):
        return len(self.tokens)

    def __iter__(self):
        for token in self.tokens:
            yield token

    def process(self):

        # Init vars from tune header
        if self.tune:
            tune = self.tune
            defaultNoteLength = tune.default_note_length
            timeSignature = tune.timeSignature
            userDefined = dict(tune.user_defined if tune.user_defined else DEFAULT_SYMBOLS)
            keySignature = tune.keySignature
        else:
            defaultNoteLength = 0.5
            timeSignature = None
            userDefined = {}
            keySignature = None

        lastNote: Optional[ABCGeneralNote] = None
        lastTuplet : Optional[ABCTuplet] = None
        lastTie: Optional[ABCTie] = None
        lastGrace: Optional[ABCGraceStart] = None
        lastBrokenRythm : Optional[ABCBrokenRhythm] = None
        expressions : List[ABCExpression] = []
        articulations : List[ABCArticulation] = []
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

                token.activeDefaultQuarterLength = defaultNoteLength
                token.activeKeySignature = keySignature
                token.applicableSpanners = self.activeSpanners[:]  # fast copy of a list

                # Attached the collected articulations to notes & chords
                token.articulations = articulations
                articulations = []

                # Attached the collected expressions to to notes & chords
                token.expressions = expressions
                expressions = []

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
                    defaultNoteLength = token.getDefaultQuarterLength()
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


class ABCHeader():
    def __init__(self):
        self.header = []
        self.defaultNoteLength = None
        self.timeSignature = None
        self.keySignature = None
        self.userDefined = {}
        self.composer = None
        self.origin = None
        self.title = None
        self.tempo = None
        self.title = None
        self.book = None

    def process_header(self):
        for token in self.header:
            if token.isMeter():
                ts = token.getTimeSignatureObject()
                if ts:
                    self.timeSignature = ts
                    if self.defaultNoteLength is None:
                        self.defaultNoteLength = token.getDefaultQuarterLength()
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

        if self.defaultNoteLength is None:
            self.defaultNoteLength = 0.5


class abcTune(ABCHandler, ABCHeader):
    '''
    Handles all tokens belonging to an ABCTune

    An ABCTuneHandler maintains a token lists and a dictonary for abcVoice Handlers.
    '''
    def __init__(self, tokens: List[ABCToken], tune_book: Optional['abcTuneBook']=None):
        self.voices = {}
        self.tune_book = tune_book
        # Set the default voice
        active_voive_tokens = []
        self.voices['1'] = active_voive_tokens
        all_voice_tokens = []

        tokenIter = iter(tokens)
        for token in tokenIter:
            if isinstance(token, ABCMetadata):
                if token.tag in "ABCDFGHILMmNOPQRSTrSUWZ":
                    self.header.append(token)
                    continue
                elif token.isKey():
                    # Stop, regular end of the tune header
                    self.header.append(token)
                    break
                elif token.isVoice():
                    # Voice field in the header
                    vid = token.getVoiceId()
                    if vid:
                        if vid == '*':
                            # this is for all voices
                            all_voice_tokens.append(token)
                            for v in self.voices.values():
                                v.append(token)
                            continue
                        elif vid in self.voices:
                            # change active voice
                            active_voive_tokens = self.voices[vid]
                        else:
                            # create new voice, start with the tokens for all voices
                            active_voive_tokens = all_voice_tokens[:]
                            self.voices[vid] = active_voive_tokens

                        # append token to the active voice
                        active_voive_tokens.append(token)
                    continue
                elif token.tag in "sw":
                    # Stop, this is a body Metatag
                    active_voive_tokens.append(token)
                    break
                continue

            # Stop, not a Metatag, so the tune body starts
            break

        # The Tune Body starts
        for token in tokenIter:
            if isinstance(token, ABCMetadata):
                if token.isVoice():
                    vid = token.getVoiceId()
                    if vid:
                        if vid == '*':
                            # no all voice notation in the body
                            continue
                        if vid in self.voices:
                            active_voive_tokens = self.voices[vid]
                        else:
                            active_voive_tokens = []
                            self.voices[vid] = active_voive_tokens

            active_voive_tokens.append(token)

        # Last Step, create Voice Handler for each voice
        self.voices =  [ abcVoice(tokens=v, tune=self) for v in self.voices.items() ]

    def process(self):
        # Get some of the default values from the tune book
        if self.tune_book:
            tune_book = self.tune_book
            self.defaultNoteLength = tune_book.defaultNoteLength
            self.timeSignature = tune_book.timeSignature
            self.userDefined = dict(tune_book.userDefined)
            self.composer = tune_book.composer
            self.origin = tune_book.origin
            self.book = tune_book.book

        # process own header data
        self.process_header()

        # process the voices
        for voice in self.voices:
            voice.process()

    def __len__(self):
        return len(self.voices) + len(self.header)

    def __iter__(self):
        for metatag in self.header:
            yield metatag

        for tune in self.voices:
            yield tune


class abcTuneBook(ABCHandler, ABCHeader):
    '''
    Handles all tokens belonging to an ABC FileHeader

    An ABCFile maintains a token list for the FileHeader
    and an Dictonary for ABCTunes of the ABCFile.
    '''
    def __init__(self, tokens: List[ABCToken]):
        '''
        Any metadata token until the first referencenumber belongig to the FileHeader.

        Fallbacks:
        If there is a non metadata field before the first reference number or an metadataf ield not allowed
        in an abcFileheader all the collected Tokens belong to an implizit Tune with 'X:1' as
        reference number. If later an X: Field with the same Refrencenumber appears did we (recursively)
        increase the Refrencenumbers until we have no conflicts.
        '''
        self.header : List[ABCMetadata] = []
        self.tunes : Dict[ABCToken] = {}

        # Hanlder of the active tune in the token stream
        active_tune: List[ABCToken] = []
        tokenIter = iter(tokens)
        ref_id = 1
        for token in tokenIter:
            if isinstance(token, ABCMetadata):
                if token.tag in "ABCDFGHILMmNORSrSUZ":
                    self.header.append(token)
                elif token.tag == 'X':
                    # Regular start of the first tune
                    active_tune = [token]
                    self.tunes[token.data] = active_tune
                    break
                elif token.tag in "KPQsTVWwZ":
                    # This token is not allowed in the header of an AbcTuneBook
                    # but its also not an explizit start of an tune
                    # implizit start of an ABCTune
                    break
                else:
                    # This metdata tag is unknwon at all, skip it.
                    continue
            else:
                # This token is not allowed in the headr of an AbcTuneBook
                # but its also not an explizit start of an abc tune
                # implizit start of an ABCTune
                break
        else:
            # This abcfile contains only Metadata, maybe an abc header include file
            return

        if active_tune is None:
            # This is not a regular abc tune book.
            # create a ref number metadata token for the tune
            active_tune = [ABCMetadata(f'X:{ref_id}')]

        # Sort the rest tokens to tunes
        for token in tokenIter:
            if isinstance(token, ABCMetadata):
                if token.tag == 'X':
                    # create a tune with all the collected tockens
                    self.tunes[ref_id] = abcTune(tokens=active_tune, tune_book=self)
                    # Find a free ref_id
                    ref_id = self.find_next_refnum(token.data)
                    token.data = str(ref_id)
                    active_tune = [token]
                    continue

            active_tune.append(token)

        self.tunes[ref_id] = abcTune(tokens=active_tune, tune_book=self)

    def process(self):
        self.process_header()

    def find_next_refnum(self, numstr: str) -> int:
        numstr = ''.join(i for i in numstr if i.isdigit())
        l = len(self.tunes)
        if not numstr:
            # No a number, take the last number + 1
            num = l
        else:
            num = int(numstr)

        # if the number is not already in in_use
        if num not in self.tunes:
            return num

        # start with until we found a free number
        return next(n for n in range(l, 2 * l + 1) if n not in self.tunes.keys())

    def __len__(self):
        return len(self.tunes) + len(self.header)

    def __iter__(self):
        for metatag in self.header:
            yield metatag

        for tune in self.tunes:
            yield tune

        pass


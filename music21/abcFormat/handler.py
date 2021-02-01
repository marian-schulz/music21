from music21.abcFormat.tokens import *

from typing import *
from collections import defaultdict
from copy import deepcopy

class ABCHandlerException(exceptions21.Music21Exception):
    pass


class ABCHandler():
    """
        The ABCHandler is just a BaseClass for implementig Handler.
    """

    def __init__(self, src: Union[List[ABCToken], str], abc_version: ABCVersion=None):
        """
        Baseclass of ABC format handlers for ABCTokens.

        Arguments:
            src:
                an ABCToken list or a abc string

            abcVersion:
                Version of the ABC Format
        """
        self.abc_version = abc_version
        self._user_defined = {}

        if isinstance(src, str):
            if abc_version is None:
                self.abc_version = parseABCVersion(src)
            self._tokens = abcTokenizer(src, abcVersion=self.abc_version)
        else:
            self._tokens = src

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
        if not self._tokens:
            return False

        return any(isinstance(t, (ABCGeneralNote)) for t in self)

    def process(self, *args):
        tokens = self._tokens
        self._tokens = []
        for token in self._process_tokens(tokens):
            self._tokens.append(token)

    @property
    def tokens(self) -> List[ABCToken]:
        return self._tokens

    def _process_tokens(self, tokens: List[ABCToken]) -> Iterator[ABCToken]:
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

        for token in tokens:
            # Lookup a redefinable symbol and continue the processing
            if isinstance(token, ABCSymbol):
                try:
                    # there are maybe more than one token in the definition
                    token = ABCSymbol.lookup(self._user_defined)
                except KeyError:
                    environLocal.printDebug(['ABCSymbol "{self.src}" without definition found.'])
                    continue

            # Each ABCToken should have his own rule method
            # @TODO: Token without p_<token> method should proccesed by
            # p_ABCToken
            method = f'p_{token.__class__.__name__}'
            try:
                method = getattr(self, method, None)
                if method is not None:
                    result = method(token)
                    if result is None:
                        # Absolute ok, if this method is not relevant anymore
                        environLocal.printDebug([f'Method "{method}" returns "None"'])
                        continue

                yield token
            except TypeError:
                environLocal.printDebug([f'Method "{method}" is not defined'])
                yield token

    def __add__(self, other):
        '''
        Return a new handler adding the tokens in both
        Contrived example appending two separate keys.

        Used in polyphonic metadata merge
        '''
        return self.__class__(self.tokens + other.tokens(), self.abcVersion)

    def __len__(self):
        return len(self._tokens)

    def __iter__(self):
        yield from self._tokens

    def __str__(self):
        return "\n".join(str(t) for t in self.tokens)


class ABCHeader(dict, ABCHandler):
    """
    The ABCHeader handles ABCToken for headers and manage the
    ABC Heade informations as attribute dictonary

    """

    __slots__ = ()
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

    def __init__(self, src: Union[List[ABCToken], str], abc_version=None):
        ABCHandler.__init__(self, src, abc_version)
        self['abcDirective'] = {}
        self['user_defined'] = {}
        self['voice'] = defaultdict(list)
        self._all_voices = []

    def p_ABCDirective(self, token: ABCDirective):
        self['abcDirective'][token.key] = token.value

    def p_ABCField(self, token: ABCField):
        if token.isInstruction():
            k, v = token.get_instruction()
            self.abcDirective[k] = v
        elif token.isVoice():
            voice_id, data = token.get_voice()
            if voice_id:
                if voice_id == '*':
                    self._all_voices.append(token)
                    for v in self.voices.values():
                        v.append(token)
                else:
                    self.voice[voice_id].append(token)
        elif token.isUserDefined():
            k, v = token.get_user_defined()
            self.user_defined[k] = v
        else:
            self[token.name] = token.value()

    def process(self, parent: Optional['ABCHeader']=None):
        if parent:
            self.update(deepcopy(parent))
        super().process()

    def __getattr__(self, attribute):
        k = list(self.keys())
        return dict.get(self, attribute, None)


class ABCBarHandler(ABCHandler):
    """
        The ABCHandler did distinguish between body and header tokens but
        does not know polyphonic ABC tune structures.

        The ABCHandler is suitable for short ABC Snippets and is basis
        for the further more complex ABC Handler.
    """

    def __init__(self, tokens, leftBar, rightBar: Optional[ABCBar] = None):
        # tokens are ABC objects in a linear stream
        super().__init__(tokens)
        self.leftBar =  leftBar
        self.rightBar = rightBar

    def __str__(self):
        return "".join(str(s) for s in self.tokens)

    def __iter__(self):
        yield from self._tokens

class ABCVoice(ABCHandler):
    """
    The ABCVoiceHandler is for representing and processing melodic tokens
    in a voice.
    """
    def __init__(self, src: Union[List[ABCToken], str], abcVersion: ABCVersion = None):
        super().__init__(src, abcVersion)
        self.measures: List[ABCBarHandler] = []

        # main proccess context variable
        self._meter = None
        self._key = None
        self._abcDirectives = {}
        self._unit_note_lengthh = None
        self._user_defined = {}

        # additional proccess context variables
        self._lastNote: Optional[ABCGeneralNote] = None
        self._lastTuplet: Optional[ABCTuplet] = None
        self._lastTie: Optional[ABCTie] = None
        self._lastGrace: Optional[ABCGraceStart] = None
        self._lastBrokenRythm: Optional[ABCBrokenRhythm] = None
        self._lastExpressions: List[ABCExpression] = []
        self._lastArticulations: List[ABCArticulation] = []
        self._lastBarToken = None
        self._activeParens = []
        self._activeSpanners = []
        self._accidentalized = {}

    def process(self, id: str = '1', tune: Optional['ABCTune'] = None):
        if tune:
            tune_header = tune.header
            # Get default value from the tune header
            self._meter = tune_header.meter
            self._key = tune_header.key
            self._abcDirectives = deepcopy(tune_header.abcDirectives)
            self._unit_note_length = tune_header.unit_note_length
            self._user_defined = dict(tune_header.user_defined)
            # Special processing for voice specific header fields
            self._process_header(tune.header.voice.get(id, []))

            # Process the token list using process from ABCHandler
            super().process()

        # @TODO: is a hack, can we trigger this state with changing diretctives ?
        # self.propagation = self._accidentalPropagation()
        self._propagation = 'not'

        super().process()

        if not self.measures:
            environLocal.printDebug(['The tokens of this handler do not define any bars'])
        elif self._tokens:
            # Last measure maybe ends without  autofinish the last bar
            self.measures.append(
                ABCBarHandler(self._tokens, leftBar=self._lastBarToken)
            )
            self._tokens = []

    def _process_header(self, tokens: List[ABCField]):
        for token in tokens:
            if not token.isVoice():
                environLocal.printDebug(f'Not a "V:" field token : "{token}"')
            # @TODO: needs implementation
            pass

    def p_ABCToken(self, token: ABCToken):
        return token

    def p_GeneralNote(self, token: ABCGeneralNote):
        """
            process a
        """
        token.activeDefaultQuarterLength = self._unit_note_length
        token.activeKeySignature = self._key
        token.applicableSpanners = self._activeSpanners[:]

        # Attached the collected articulations to notes & chords
        token.articulations = self._lastArticulations
        self._lastArticulations = []

        # Attached the collected expressions to to notes & chords
        token.expressions = self._lastExpressions
        self._lastExpressions = []

        if self._lastTie is not None:
            token.tie = 'stop'
            _lastTie = None

        if self._lastBrokenRythm:
            self._lastBrokenRythm.set_notes(self._lastNote, token)
            self._lastBrokenRythm = None

        if self._lastGrace is not None:
            token.inGrace = True
        if self._lastTuplet is None:
            pass
        elif self._lastTuplet.noteCount == 0:
            self._lastTuplet = None
        else:
            self._lastTuplet.noteCount -= 1  # decrement
            # add a reference to the note
            token.activeTuplet = self._lastTuplet.m21Object()

        return token

    def p_ABCChord(self, token: ABCChord):
        self.p_GeneralNote(token)
        token.subTokens = list(super().process(token.subTokens))
        return token

    def p_ABCRest(self, token):
        pass

    def p_ABCNote(self, token: ABCNote):
        self.p_GeneralNote(token)
        if token.accidental:
            # Remember the accidental of this note
            if self._propagation == 'octave':
                self._accidentalized[(token.pitch_name, token.octave)] = token.accidental
            elif self._propagation == 'pitch':
                self._accidentalized[token.pitch_name] = token.accidental
            else:
                # Lookup the active accidentals
                if self._propagation == 'pitch' and token.pitch_name in self._accidentalized:
                    token.carriedAccidental = self._accidentalized[token.pitch_name]
                elif self._propagation == 'octave' and (token.pitch_name, token.octave) in self._accidentalized:
                    token.carriedAccidental = self._accidentalized[(token.pitch_name, token.octave)]

        # remember this note/chord
        self._lastNote = token
        return token

    def p_ABCField(self, token: ABCField):
        # @TODO: special cases like user_defined
        setattr(self, f'_{token.name}', token.value())

    def p_ABCBrokenRhythm(self, token: ABCBrokenRhythm):
        if self._lastNote:
            self.lastBrokenRythm = token

    def p_ABCTuplet(self, token: ABCTuplet):
        token.updateRatio(self._meter)
        token.updateNoteCount()
        self._lastTuplet = token
        self._activeParens.append('Tuplet')

    def p_ABCSpanner(self, token: ABCSpanner):
        self._activeSpanners.append(token.m21Object())
        self._activeParens.append(token)
        return token

    def p_ABCParenStop(self, token: ABCParenStop):
        if self._activeParens:
            p = self._activeParens.pop()
            if isinstance(p, ABCSpanner):
                self._activeSpanners.pop()
                # the Stop Token is no longer needed
        return token

    def p_ABCTie(self, token: ABCTie):
        if self._lastNote and self._lastNote.tie == 'stop':
            self._lastNote.tie = 'continue'
        elif self._lastNote:
            self._lastNote.tie = 'start'
        self._lastTie = token

    def p_ABCGraceStart(self, token: ABCGraceStart):
        self._lastGrace = token

    def p_ABCGraceStop(self, token: ABCGraceStop):
        self._lastGrace = None

    def p_ABCArticulation(self, token: ABCArticulation):
        self._lastArticulations.append(token)
        # this token is no longer needed

    def p_ABCExpression(self, token: ABCArticulation):
        self._lastExpressions.append(token)

    def p_ABCBar(self, token: ABCBar):
        self._accidentalized = {}
        if self._tokens:
            self.measures.append(
                ABCBarHandler(self._tokens, leftBar=self._lastBarToken, rightBar=token)
            )
            self._tokens = []
        self._lastBarToken = token

    def p_ABCDirective(self, token: ABCDirective):
        self._abcDirectives[token.key] = token.value

    def __len__(self):
        return len(self._tokens) + len(self.header)

    def __iter__(self):
        if self._tokens:
            yield from self._tokens
        else:
            for measure in self.measures:
                yield from measure

    def str(self):
        return "\n".join(str(b) for b in self)


class ABCTune(ABCHandler):
    '''
    """
    The ABCPolyponicHandler distinguishes between multiple voices
    in an ABC Tune, but it does not distinguish between multiple
    tunes in the input.

    Use the ABCTuneBookHandler to proccess tunebooks with multible
    tunes in the input.
    """
    '''
    def __init__(self, src: Union[List[ABCToken],str], abc_version=None):
        super().__init__(src=src, abc_version=abc_version)
        header, body =  ABCTune._split_header_and_body(self.tokens)
        voices =  {
            vid: ABCVoice(src=t, abcVersion=abc_version) for vid, t in ABCTune._split_voices(body).items()
        }

        self.header = ABCHeader(src=header)
        self.voices: Dict[str, ABCVoice] = voices

    @property
    def body(self) -> Union[List[ABCToken], Dict[str, ABCVoice]]:
        return self.voices[0] if len(self.voices) == 1 else self.voices

    @classmethod
    def _split_header_and_body(self,tokens) -> Tuple[List[ABCToken], List[ABCToken]]:
        header: List[ABCToken] = []
        body: List[ABCToken] = []
        tokenIter = iter(tokens)

        for token in tokenIter:
            if isinstance(token, ABCField):
                header.append(token)
                if token.isKey():
                    # Stop, regular end of the tune header
                    break
            elif isinstance(token, ABCDirective):
                header.append(token)
            else:
                # Not a valid token in Header
                # Not a regular synatx, but
                # We asume the body starts here
                body.append(token)
                break

        # put the rest into the Body
        body.extend(tokenIter)
        return header, body

    @classmethod
    def _split_voices(cls, tokens: List[ABCToken]) -> Dict[str,  List[ABCToken]]:
        active_voice = []
        voices = { '1': active_voice }

        for token in tokens:
            if isinstance(token, ABCField):
                if token.isVoice():
                    voice_id, _ = token.get_voice()
                    if voice_id:
                        if voice_id in voices:
                            active_voice = voices[voice_id]
                        else:
                            active_voice = []
                            voices[voice_id] = active_voice
                    continue
            active_voice.append(token)
        return voices

    def process(self, tune_book: Optional['ABCTuneBook'] = None):
        self.header.process(parent=tune_book)
        # process the voices
        for voice_id, voice in self.voices.items():
            voice.process(id=voice_id, tune=self)

    def __len__(self):
        return len(self.header) + sum(len(voice) for voice in self.voices)

    def __iter__(self):
        yield from self.header.tokens
        for voice in self.voices.values():
            yield from voice

    def __str__(self):
        yield from self.header
        for voice in self.voices:
            yield from voice
        d = list(self)
        return "\n".join(str(s) for s in self)


class ABCTuneBook(ABCHandler):
    '''
    Handles all tokens belonging to an ABC FileHeader

    An ABCFile maintains a token list for the FileHeader
    and an Dictonary for ABCTunes of the ABCFile.
    '''
    def __init__(self, src: Union[List[ABCToken], str], abc_version: Optional[ABCVersion]=None):
        '''
        Any metadata token until the first referencenumber belongig to the FileHeader.

        Fallbacks:
        If there is a non metadata field before the first reference number or an metadataf ield not allowed
        in an abcFileheader all the collected Tokens belong to an implizit Tune with 'X:1' as
        reference number. If later an X: Field with the same Refrencenumber appears did we (recursively)
        increase the Refrencenumbers until we have no conflicts.
        '''
        super().__init__(src, abc_version=abc_version)
        header, tunes = ABCTuneBook.split_header_and_tunes(self.tokens)
        self.header : ABCHeader = ABCHeader(src=header, abc_version=None)
        self.tunes: Dict[int, ABCTune] = { ref_num: ABCTune(src=t, abc_version=abc_version)
                                          for ref_num, t in tunes.items()}

    def parse(self):
        pass

    @classmethod
    def split_header_and_tunes(cls, tokens: List[ABCToken], strict_abc=True):
        # token list for the header
        header : List[ABCToken] = []
        tunes: Dict[str, List[ABCToken]] = {}
        active_tune: List[ABCToken] = []
        tokenIter = iter(tokens)

        for token in tokenIter:
            if isinstance(token, ABCDirective):
                # abc directive in the tune book header
                header.append(token)
                continue
            elif isinstance(token, ABCField):
                if token.tag in "ABCDFGHILMmNORSrSUZ":
                    header.append(token)
                    continue
                elif token.isReferenceNumber():
                    # regular end of the tunebook and start of the first tune
                    active_tune = [token]
                    tunes[token.get_reference_number()] = active_tune
                    break
            # Some tokens not belonging to a tune book header
            if strict_abc:
                raise ABCHandlerException(f'Irregular header field "{token.tag}" in Tunebook.')
            else:
                environLocal.printDebug([f'Irregular header field "{token.tag}" in tunebook. Continue with tunes.'])
                active_tune = [token]

        else:
            # This abc tune book contains only Metadata, maybe an abc header include file
            environLocal.printDebug([f'The tunebook contains no tunes (maybe an include file)'])
            return header, tunes

        # split in tune
        for token in tokenIter:
            if isinstance(token, ABCField) and token.tag == 'X':
                active_tune = [token]
                tunes[token.data] = active_tune
            else:
                active_tune.append(token)

        return header, tunes

    def process(self):
        self.header.process()
        for tune in self.tunes.values():
            tune.process()

    def __len__(self):
        return sum(len(t) for t in self.tunes.values()) + len(self.header)

    def __iter__(self):
        yield from self.header.tokens

        for tune in self.tunes.values():
            yield from tune

    def __str__(self):
        return '\n'.join(str(t) for t in self)


from music21 import clef
from music21 import common
from music21 import environment
from music21 import exceptions21
from music21 import meter
from music21 import stream
from music21 import spanner
from music21 import harmony
from music21 import metadata

def append_tokens(handler: ABCHandler, s: stream.Stream):
    for token in handler.tokens:
        m21obj = token.m21Object()
        if m21obj is None:
            environLocal.printDebug(f'Got no m21Object from "{token}".')
        else:
            s.append(m21obj)

def translate(src: str):
    abcVersion = parseABCVersion(src)
    tune_book = ABCTuneBook(src, abcVersion)
    tune_book.process()
    m21streams = []
    for tune in tune_book.tunes.values():
        m21tune_score = stream.Score()
        m21tune_score.insert(0, metadata.Metadata())

        if tune.header.title:
            m21tune_score.metadata.title = tune.header.title
        if tune.header.composer:
            m21tune_score.metadata.composer = tune.header.composer


        for voice in tune.voices.values():
            m21part = stream.Part()
            if voice.measures:
                for measure in voice.measures:
                    m21measure = stream.Measure()
                    append_tokens(measure, m21measure)
                    m21part.append(m21measure)

            elif voice.tokens:
                append_tokens(voice, m21part)
            else:
                environLocal.printDebug('Voice has no tokens or measures')

            m21tune_score.append(m21part)
        m21tune_score.show()
    return tune_book


if __name__ == '__main__':
    from music21.abcFormat import testFiles
    translate(testFiles.fyrareprisarn)


# Summary
## Refactoring
Type hints are now widespread in the code. Variable names are often longer and
more meaningful. The code is more pythonic.

The aim of this implementation was to make the ABC converter easier to adapt new functionality.
To do this, it was necessary to make some basic changes and rewrite many code that was 
difficult to read and understand.

As a small extra bonus, the abc import is now also faster.

**The biggest disadvantage:**

Some interfaces has changed so exisitig code based on the ABCFormat will need an adaption.
Nevertheless, the interface to the music21 converter module has remained the same, 
so that the code of this ABC format variant can be exchanged without further adjustment

## Added functionality
* Extendet support of the abc KeySignatures syntax.
* Support for complex meters.  
* Most of the abc decorations are now supported.
* Basic support for non chord symbol text annotations
* Lyrics support is added. (w: but not W:)
* Most of the clefs are supported
* Support for stave names
* User defined symbols (U:) are supported
* Support for instrumentation through the midi directive


## Fixed bugs
* Spanners will work with grace notes
* Better accidental propagation for chords. 
* When using transposing clefs the pitchClass and the accidental displayStatus remains as intendet.
* Stop leaking status changes in one voice to other voices (more general problem)

## Workarounds
* All notes of a tune with free meter and without any bars are placed in one measure insteat of a Part
This avoids the music21 exporter setting a 4/4 meter and call makeMeasures.

## TODO
* Support for voice grouping
* Support symbol lines
* Support for overlay voices
* Support for the voice property 'stem' (stemDirection)
* Complete abc directive support
* Support for some style directives
* Support for macros (m:)
* Better support for ABCVersion related dialects & outdated syntax
* Support for the special utf-8 charakters for accidentials
* Support for latex encodet language related symbols in lyrics and information field
This is an usecase for importing abc code written for abc2ps
* Support for html encodet language related symbols (supported accents & ligatures) in text strings
* Support the abc parts synatx (P:)
# tokenizer
The tokenizer is bases on regular expressions. The regular expressions are
defined with the token class.

# ABCHandler
The processing code for the abc handler has moved to the ABCTokenProcessor.
The tokenizer code has moved to a seperat normal function. 

Many functions were no longer needed and have therefore been dropped,
other functions have been revised and partly rewritten. None of the functions
iterates over lists via index, but without exception via iterator. 

## Processing
For a correct result, it is now necessary to split the tokens into voices first and 
to process each voice separately. This guarantees that status changes that are only 
meant for one voice do not get into the context of another voice.

When the ```process``` method of an ABCHandler instance is called, it returns an 
ABCTune NamedTuple containing an ABCHandlerVoice object with processed tokens for each 
voice and separately all metadata tokens of the header.

# ABCHandlerVoice
This ABCHandler has a voiceID property, and should only contain token belonging to one
voice and common metadata from the header. All tokens that cannot be assigned to a voice, 
by mistake (bad abc code) or because no voices are defined are assigned to voiceId '1'. 

The process method of ABCHandlerVoice will call the ABCTokenProcessor.
  
# Token processor
Token processing is done by an Instance of ABCTokenProcessor.
The token processor is easy to maintain and extend. Each token
that needs processing has an object method with the name
```process_{ABCTokenClassName}(self, token)```. The metode is selected
according to the class name of the token, if not available a method
for the base class of the token is searched for.

**TODO:**
* get better performance for downstream processes when tokens that are no 
longer needed are discarded. 

# ABCTokens
Is the baseclass of all ABCTokens. 
All classes derived from ABCToken that represent a musci21 object should implement 
the method ```m21Object()```, all other classes can stay with the m21Object implementation 
provided by ABCToken, which simply returns ```None```.
The methods ```parse``` and ```preparse``` have been removed, everything necessary is now done
in the object initialisation. Therefore, the icreation of tokens without a token string is no 
longer possible and would immediately lead to errors with some token.

Spanner tokens now have a supper class ABCTokenSpanner, as do expressions,
articulations and marks. Metadatas now almost all have their own class derived
from ABCMetadata. Notes, rests and chords have their own classes and a common
supper class (```ABCGeneralNote```). Some token classes have a factory class based on 
the common regular expression schema.

## Metadata
The metadata class has been divided into several subclasses. This accommodates multiplexing
in the token processor, the functions for querying the data type are no longer needed. Metadata
tokens are created by a factory object 'ABCField' which defines a common regular expression for
the tokenizer.

Inline metadata is recognised and marked as such.

The following classes derived from ABCMetadata have no special implementations and exist only 
for the purpose of multiplexing in the ABCTokenProcessor and ABCTranslator:
* ABCReferenceNumber
* ABCTitle
* ABCOrigin
* ABCComposer

### ABClef
ABCKey (K: ) and ABCVoice (V:) share the mixin class ABCClef.
Clef currently evaluates and provides the clef type and octave modificator. The cleftyp is evaluated
using a dictionary and currently knows 23 common clef designations. The property 'octaveChange'
of the m21Clef is evaluated as well as the abc clef keyword 'octave'. Additionally, the common clef
option '-8va' is recognised.

The transposing of the meldoy by octaves is done in the translator before the music21 object is created.

### ABCKey
The abc syntax for KeySignatures has been extended.  The keysignature can now be explicitly changed
by notes with accidentals.

ABCKey is extendet by the ABCClefMixin.

### ABCMeter
Now handles the syntax for complex TimeSignatures with multiple nominators.
 
Example:
```3+2/4 == (3+2)/4 == 5/4```

The abc standard does not 
recognise time signatures with different denominators.

### ABCTempo
The token **ABCVoice** represents the abc keyword 'Q:' and handles MetronomeMark Objects
The implementation ist still the orginal but got a small change to optimize the placement
for MuseScore.

### ABCLyrics
The token **ABCLyrics** represent the abc field *w:* and hold a line of lyrical data.
The lyrics are appended to the first note relevant to the lyrics during processing. 

Multiverse lyrics are supported.

### ABCUnitNoteLength
The token **ABCLyrics** represent the abc field *L:*.
It is used to set the default length of an abc note. 

Implementation has not changed.

### ABCVoice
The token **ABCVoice** represents the abc keyword 'V:'.
The keyword is examined for the voiceID (mandatory field) and optionally for 
'name' and 'subname'.

The 'name' and 'subname' are assigned to the ```stream.Part``` object properties ```partName``` 
and ```partAbbreviation``` respectively.

ABCVoice is extendet by the ABCClefMixin.

### ABCUserDefinition
The token **ABCUserDefinition** represents the abc keyword 'U:'.
This keyword can be used to define user-defined symbols. Usually, it should be used to create 
abbreviations for decorations. However, since the definition in the field is tokenised 
unfiltered, it can also be used to create abbreviations for note sequences or chords.

### ABCInstruction
The token **ABCInstruction** represents the abc keyword 'I:'.
So far, only instructions for accidential propagation and midi instruments are supported.

## ABCDirective
The class **ABCDirective** handles ('%% directive') and is a factory for ABCInstructions.
Example:
```
>>> ABCDirective('%%instruction code')
<ABCInstruction 'I:instruction code'>
```
@TODO: Cache the ABCDirective object and use the ```__call__``` interface 

## ABCDecoration
The class **ABCDecoration** is a factory class for the abc decoration syntax.
The regular expression of ABCDecoration captures all abc expressions enclosed by an 
exclamation mark and the dot '.', This includes spanner (Dim, Cresc) as well as known 
annotations, marks, expressions, articulations and dynamics.

For practical reasons **ABCDecoration** is an instance of ABCToken but it will never show up 
in any Tokenlist or is super class of any other ABCToken type.

| abc decoration (src)  | ABCToken object |
|-----------------|-----------------------|
|!crescendo(!     | ABCCrescStart(src)    |
|!<(!             | ABCCrescStart(src)    |
|!crescendo)!     | ABCParenStop(src)     |
|!<)!             | ABCParenStop(src)     |
|!diminuendo(!    | ABCDimStart(src)      |
|!>(!             | ABCDimStart(src)      |
|!diminuendo)!    | ABCParenStop(src)     |
|!>)!             | ABCParenStop(src)     |
|!staccato!       | ABCArticulation(src, articulations.Staccato)     |
|!downbow!        | ABCArticulation(src, articulations.DownBow)      |
|!uppermordent!   | ABCExpression(src, expressions.InvertedMordent)  |
|!pralltriller!   | ABCExpression(src, expressions.InvertedMordent)  |
|!lowermordent!   | ABCExpression(src, expressions.Mordent)          |
|!mordent!        | ABCExpression(src, expressions.Mordent)          |
|!upbow!          | ABCArticulation(src, articulations.UpBow)        |
|!emphasis!       | ABCArticulation(src, articulations.Accent)       |
|!accent!         | ABCArticulation(src, articulations.Accent)       |
|!straccent!      | ABCArticulation(src, articulations.StrongAccent) |
|!tenuto!         | ABCArticulation(src, articulations.Tenuto)       |
|!fermata!        | ABCExpression(src, expressions.Fermata)          |
|!trill!          | ABCExpression(src, expressions.Trill)            |
|!coda!           | ABCMark(src, repeat.Coda)   |
|!segno!          | ABCMark(src, repeat.Segno)  |
|!snap!           | ABCArticulation(src, articulations.SnapPizzicato)|
|!.!              | ABCArticulation(src, articulations.Staccato)     |
|!>!              | ABCArticulation(src, articulations.Accent)       |
|!D.S.!           | ABCAnnotations('_D.S.')     |
|!D.C.!           | ABCAnnotations('_D.C.')     |
|!dacapo!         | ABCAnnotations('^DA CAPO')  |
|!fine'!          | ABCAnnotations('^FINE')     |
|!p!, !pp!, !ppp!, !pppp!, !f!, !ff!, !fff!, !ffff!, !mp!, !mf!, !sfz!| ABCDynamic(src) |
|!1! - !5!        | ABCArticulation(src, articulations.Fingering)     |

There is some untested implementation stripping not only the '!' but also the '+' as enclosing symbol 
defined by abc 2.0.  

@TODO: 
* Do not create an extra ABCToken object for each decoration found in abc code. (they will never change)
* Cache the ABCDecoration object and use the '__call__' interface in the tokenizer. 
* Add the few missing decorations 
* Howto translate the 'Irish roll (~)'

## ABCMark
The class ABCMark is a superclass for all tokens that are inserted into the stream as 
objects in music21 direct.

### ABCAnnotation
Annotations of abc are enclosed in double quotes and differ from Chordsymbols in that 
the first text character is a symbol of ```@^_<>```
Das erste symbol beschreibt die Plazierung der Annotation.

The following text is inserted as ```expression.TextExpression``` in the corresponding 
stream object.
So far, placement is only rudimentarily implemented for ```_``` and ```^```.

ABCAnnotation is derived from ABCMark

### ABCChordsymbol
The **ABCChordsymbol** token represents a chord symbol and is enclosed in double quotes in the abc code.
The chord symbols have been decoupled from note objects and now have their own token.

ABCChordsymbol is derived from ABCMark

### ABCAnnotation
Annotations of abc are enclosed in double quotes and differ from Chordsymbols in that 
the first text character is a symbol of ```@^_<>```
Das erste symbol beschreibt die Plazierung der Annotation.

The following text is inserted as ```expression.TextExpression``` in the corresponding 
stream object.
So far, placement is only rudimentarily implemented for ```_``` and ```^```.

ABCAnnotation is derived from ABCMark

## ABCExpression
The class **ABCExpression** represent expression properties of notes & chords.
It has an m21Class property with which it can create all currently implemented expressions. 
If necessary, it can also serve as a superclass for expressions with additional functionality.

## ABCArticualtion
The class **ABCArticualtion** represent articulation properties of notes & chords.
It has an m21Class property with which it can create all currently implemented articulationen. 
If necessary, it can also serve as a superclass for articulations with additional functionality.

## ABCSymbol
The **ABCSymbol** represent one letter Symbols (H-W, h-w and the symbol ~) in abc code.
Some of the symbols are predefined but all of them can redefined by the abc field 'U:'.

## ABCGeneralNote
The class **ABCGeneralNote** is super class of ABCNote, ABCChord and ABCRest. 
It implements the parsing of the abc length modifier. The handling of tuplets, 
ties, spanner, articulations and expressions are moved here too. (has happened 
before in the tranlsator)

Instead of the ABCBrokenrythm token, the object now holds a float modifier. 
The modifier is assigned during processing.

### ABCNote
The **ABCNote** represent an abc note. It implements the parsing of accidentals, pitchclass 
and octave. It evaluates the display status of accidetals as well.

 
@TODO: check if the ```_pitchTranslationCache``` has still any benefits or maybe should
moved to ```__init__```

### ABChord
The **ABCNote** represent an abc chord.

Only ABCArticulation, ABCExpression and ABCNote are allowed as tokens in the chord inner. 
The tokens are handled in a separate TokenProcessor instance. 

Necessary values such as Keysignature, DefaultQuarterLength and active accidentials are 
passed to this TokenProcessor. Accidentals in chords now correctly become active accidentals
for the following notes and chords.

ABCChord is derived from ABCGeneralnotes.

@TODO: write and cache an special TokenProcessor for the inner tokens of a chord.
 
### ABCRest
The **ABCNote** represent an abc rest note.
The regular expression now also captures rest notes at 'x'. 

ABCRest is derived from ABCGeneralnotes.

## ABCTuplets
The **ABCNote** represent an abc tuplet.

Code has not changed.
Fix: Grace notes are now ignored (and not counted) from tuplets while processing.

@TODO: Replace ```if elif``` chain for the tuplet types with an dictonary. This could have 
performance benefits.

 



 






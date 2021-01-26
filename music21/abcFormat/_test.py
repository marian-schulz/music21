from music21 import pitch, key, note, stream

print(pitch.Pitch('c'))
print(pitch.Pitch('C'))

k = key.Key('G', 'major')
print(k.alteredPitches)
ks = key.KeySignature()
ks.alteredPitches = ['E-', 'G#']
print(ks.alteredPitches)
p = pitch.Pitch('C#')
p.accidental.displayStatus = True
n1 = note.Note(p)
p.accidental.displayStatus = False
n2 = note.Note(p)
s = stream.Stream()
n1.quarterLength = 2.0
s.append(n1)
n2.quarterLength = 1.5
s.append(n2)
print(n1)
print(n2)
s.show('text')



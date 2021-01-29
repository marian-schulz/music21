from music21 import pitch, key, note, stream, spanner

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

n1 = note.Note('C4')
n2 = note.Note('D4')
n3 = note.Note('E4')
n4 = note.Note('F4')
n5 = note.Note('G4')
n6 = note.Note('A4')
slur1 = spanner.Slur([n2, n3])
slur2 = spanner.Slur()

slur2.addSpannedElements([n5, n6])

part1 = stream.Part()
part1.append([n1, n2, n3, n4, n5, n6])
part1.insert(0, slur1)
part1.insert(0, slur2)

part1.show('text')

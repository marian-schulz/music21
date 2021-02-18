import re
import unittest

abcAccentsAndLigatures = {
    '&Aacute;': 'Á',
    '&Abreve;': 'Ă',
    '&Acirc;': 'Â',
    '&Agrave;': 'À',
    '&Aring;': 'Å',
    '&Atilde;': 'Ã',
    '&Auml;': 'Ä',
    '&Ccedil;': 'Ç',
    '&Eacute;': 'É',
    '&Ecirc;': 'Ê',
    '&Egrave;': 'È',
    '&Euml;': 'Ë',
    '&Iacute;': 'Í',
    '&Icirc;': 'Î',
    '&Igrave;': 'Ì',
    '&Iuml;': 'Ï',
    '&Ntilde;': 'Ñ',
    '&Oacute;': 'Ó',
    '&Ocirc;': 'Ô',
    '&Ograve;': 'Ò',
    '&Oslash;': 'Ø',
    '&Otilde;': 'Õ',
    '&Ouml;': 'Ö',
    '&Scaron;': 'Š',
    '&Uacute;': 'Ú',
    '&Ucirc;': 'Û',
    '&Ugrave;': 'Ù',
    '&Uuml;': 'Ü',
    '&Yacute;': 'Ý',
    '&Ycirc;': 'Ŷ',
    '&Yuml;': 'Ÿ',
    '&Zcaron;': 'Ž',
    '&aacute;': 'á',
    '&abreve;': 'ă',
    '&acirc;': 'â',
    '&agrave;': 'à',
    '&aring;': 'å',
    '&atilde;': 'ã',
    '&auml;': 'ä',
    '&ccedil;': 'ç',
    '&eacute;': 'é',
    '&ecirc;': 'ê',
    '&egrave;': 'è',
    '&euml;': 'ë',
    '&iacute;': 'í',
    '&icirc;': 'î',
    '&igrave;': 'ì',
    '&iuml;': 'ï',
    '&ntilde;': 'ñ',
    '&oacute;': 'ó',
    '&ocirc;': 'ô',
    '&ograve;': 'ò',
    '&oslash;': 'ø',
    '&otilde;': 'õ',
    '&ouml;': 'ö',
    '&scaron;': 'š',
    '&uacute;': 'ú',
    '&ucirc;': 'û',
    '&ugrave;': 'ù',
    '&uuml;': 'ü',
    '&yacute;': 'ý',
    '&ycirc;': 'ŷ',
    '&yuml;': 'ÿ',
    '&zcaron;': 'ž',
    r'\"A': 'Ä',
    r'\"E': 'Ë',
    r'\"I': 'Ï',
    r'\"O': 'Ö',
    r'\"U': 'Ü',
    r'\"Y': 'Ÿ',
    r'\"a': 'ä',
    r'\"e': 'ë',
    r'\"i': 'ï',
    r'\"o': 'ö',
    r'\"u': 'ü',
    r'\"y': 'ÿ',
    r"\'A": 'Á',
    r"\'E": 'É',
    r"\'I": 'Í',
    r"\'O": 'Ó',
    r"\'U": 'Ú',
    r"\'Y": 'Ý',
    r"\'a": 'á',
    r"\'e": 'é',
    r"\'i": 'í',
    r"\'o": 'ó',
    r"\'u": 'ú',
    r"\'y": 'ý',
    r'\/O': 'Ø',
    r'\/o': 'ø',
    r'\AA': 'Å',
    r'\HO': 'Ő',
    r'\HU': 'Ű',
    r'\Ho': 'ő',
    r'\Hu': 'ű',
    r'\^A': 'Â',
    r'\^E': 'Ê',
    r'\^I': 'Î',
    r'\^O': 'Ô',
    r'\^U': 'Û',
    r'\^Y': 'Ŷ',
    r'\^a': 'â',
    r'\^e': 'ê',
    r'\^i': 'î',
    r'\^o': 'ô',
    r'\^u': 'û',
    r'\^y': 'ŷ',
    r'\`A': 'À',
    r'\`E': 'È',
    r'\`I': 'Ì',
    r'\`O': 'Ò',
    r'\`U': 'Ù',
    r'\`a': 'à',
    r'\`e': 'è',
    r'\`i': 'ì',
    r'\`o': 'ò',
    r'\`u': 'ù',
    r'\aa': 'å',
    r'\cC': 'Ç',
    r'\cc': 'ç',
    r'\uA': 'Ă',
    r'\uE': 'Ĕ',
    r'\ua': 'ă',
    r'\ue': 'ĕ',
    r'\vS': 'Š',
    r'\vZ': 'Ž',
    r'\vs': 'š',
    r'\vz': 'ž',
    r'\~A': 'Ã',
    r'\~N': 'Ñ',
    r'\~O': 'Õ',
    r'\~a': 'ã',
    r'\~n': 'ñ',
    r'\~o': 'õ',
    '&AElig;': 'Æ',
    '&aelig;': 'æ',
    '&OElig;': 'Œ',
    '&oelig;': 'œ',
    '&szlig;': 'ß',
    '&ETH;': 'Ð',
    '&eth;': 'ð',
    '&THORN;': 'Þ',
    '&thorn;': 'þ',
    r'\AE': 'Æ',
    r'\ae': 'æ',
    r'\OE': 'Œ',
    r'\oe': 'œ',
    r'\ss': 'ß',
    r'\DH': 'Ð',
    r'\dh': 'ð',
    r'\TH': 'Þ',
    r'\th': 'þ',
}

ACCENTS_AND_LIGATURES_RE = re.compile("|".join(re.escape(c) for c in abcAccentsAndLigatures))

def encodeAccentsAndLigatures(src: str) -> str:
    founds = set(ACCENTS_AND_LIGATURES_RE.findall(src))
    for found in founds:
        r = abcAccentsAndLigatures[found]
        src = src.replace(found, r)
    return src

class Test(unittest.TestCase):
    def testAccentReMatch(self):
        '''
        Test if regular expression is matching all mnenomic charakter sequences
        '''
        for accent, utf in abcAccentsAndLigatures.items():
            self.assertIsNotNone(ACCENTS_AND_LIGATURES_RE.match(accent), (accent, utf))

    def testEncodingSelf(self):
        ist = "".join(abcAccentsAndLigatures.keys())
        soll = "".join(abcAccentsAndLigatures.values())
        self.assertEqual(encodeAccentsAndLigatures(ist), soll, (ist, soll))

    def testEncodingExample(self):
        testdata = [
            (r'Ent- z\"u - cken nur', 'Ent- zü - cken nur'),
            (r'hei- \sset', 'hei- ßet'),
            (r'z\"u z\"u', 'zü zü')
        ]
        for ist, soll in testdata:
            self.assertEqual(encodeAccentsAndLigatures(ist), soll, (ist, soll))

if __name__ == '__main__':
    unittest.main()


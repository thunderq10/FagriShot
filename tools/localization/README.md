# Editing the Arabic preview

The player builds overrides on the owner's computer from update 131072. The English reference is the US English resource tree under `Msg/USen`. The distributed catalogs contain Arabic translations, resource names, labels and fingerprints. Store the local bilingual catalog and generated overlays under ignored `work/private/`.

## Find a message

Run the following from the source checkout with its Python environment active. Set `romfs` to the extracted RomFS directory already used by the player.

```python
from pathlib import Path
from fagrishot.localization import write_catalog

romfs = Path(r"D:\GameDumps\FortunesWeave\romfs")
private = Path("work/private/localization")
count = write_catalog(romfs, private / "USen.catalog.jsonl")
print(count, "labeled message slots")
```

Each JSON line identifies one message by file and label. The raw entry fingerprint includes binary controls and its terminator. Blank slots, event directives and controller icons also have labels. Count reviewed, visibly translated entries separately from the total number of slots.

## Edit a translation

Use the format in [setup.json](../../fagrishot/arabic_data/setup.json) and [opening.json](../../fagrishot/arabic_data/opening.json). Store logical Arabic in normal reading order. Each string in a local catalog record's `parts` array takes one replacement string in the same position relative to the surrounding control tokens. Omit the token objects from the translation's array. The patcher inserts their original bytes. Preserve meaningful line breaks. A token can separate two runs on the same displayed line, so check the order of mixed Arabic and Latin, colored fragments and controller icons in the game.

Some controls encode pairs of visible alternatives inside their binary arguments. The private catalog exposes these as `choices`, indexed by the control's zero-based position in the complete source parts array. An optional translation can provide the same two choices in the same order. The patcher recalculates their UTF-8 byte lengths and preserves the command type. Help text, alternative forms, images and voiced dialogue need separate coverage checks.

## دليل الصياغة العربية

في الحوار القصصي، اختر فصحى تناسب المتكلم والمقام. حين تصف فورتونا خراب معقل للبشر، يحسن أن توحي عبارتها بالحسرة. وحين يشرح زينون عمل الحوض ونواة القلب، يحتاج القارئ إلى وصف دقيق يستطيع تتبعه. وفي القوائم، استعمل فعلا قصيرا يدل على ما سيحدث عند الضغط، مثل «اختر مستوى الصعوبة».

| المصطلح في اللعبة | الصيغة المعتمدة في حوارات البداية |
| --- | --- |
| Fortuna / Hong Hua | فورتونا / هونغ هوا |
| Sothis / Zenon | سوثيس / زينون |
| The Beginning / Voice upon the Peak | البداية / صوت القمة |
| Heart Core / Mediator | نواة القلب / الوسيط |
| underworld / forbidden land | العالم السفلي / الأرض المحرمة |

اقرأ الحوار بصوت مسموع مع الرد الذي يليه. راجع مرجع الضمير وجنس المخاطب والعدد، وتأكد من أن كل جواب في قائمة الخيارات يناسب كلام الشخصية التي سترد عليه. وإذا توقف معنى العبارة على حركة قد يصعب عرضها، فاختر لفظا أوضح. استخدم «؟» و«،» في النص العربي، واحتفظ بترتيب أحرف الرموز مثل E34 عند عرضها داخل اللعبة.

## Verify an edit

Run `python -m unittest discover -s tests -p test_localization.py -v` with the project's Python environment. It checks MSBT framing, unmodified data, placeholders, binary alternatives and the tutorial graphic's BC7 block encoding using original synthetic fixtures.

Generate a fresh overlay through the player's Arabic preview setting. An incorrect source fingerprint stops installation. Verify the edited message in the running game at the intended text scale. Include both text choices for dynamic labels and an example with punctuation or numbers. The tutorial illustration is redrawn separately against a fingerprinted local texture. All texture and font outputs belong in the private overlay.

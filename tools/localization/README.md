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

The chapter card inserts three separately rendered labels. Its fingerprinted format uses `argument_order` to arrange them for Arabic reading order. The patcher accepts only a complete permutation of bare indexed argument references separated by punctuation or spaces. Each reference keeps its original bytes.

## دليل الصياغة العربية

في الحوار القصصي، اختر فصحى تناسب المتكلم والمقام. حين تصف فورتونا خراب معقل للبشر، يحسن أن توحي عبارتها بالحسرة. وحين يشرح زينون عمل الحوض ونواة القلب، يحتاج القارئ إلى وصف دقيق يستطيع تتبعه. وفي القوائم، استعمل فعلا قصيرا يدل على ما سيحدث عند الضغط، مثل «اختر مستوى الصعوبة».

| المصطلح في اللعبة | الصيغة المعتمدة في حوارات البداية |
| --- | --- |
| Fortuna / Hong Hua | فورتونا / هونغ هوا |
| Sothis / Zenon | سوثيس / زينون |
| The Beginning / Voice upon the Peak | البداية / صوت القمة |
| Heart Core / Mediator | نواة القلب / الوسيط |
| underworld / forbidden land | العالم السفلي / الأرض المحرمة |
| Heroic Games / Flame Lords of Dagda | ألعاب الأبطال / سادة اللهب في داغدا |
| Hell's-Eye / Ravishing Shadow | ذو عين الجحيم / الظل الفاتن |
| Breaker of Armies / Siren of the Six-String | قاصمة الجيوش / ساحرة الأوتار الستة |

اقرأ الحوار بصوت مسموع مع الرد الذي يليه. راجع مرجع الضمير وجنس المخاطب والعدد، وتأكد من أن كل جواب في قائمة الخيارات يناسب كلام الشخصية التي سترد عليه. وإذا توقف معنى العبارة على حركة قد يصعب عرضها، فاختر لفظا أوضح. استخدم «؟» و«،» في النص العربي، واحتفظ بترتيب أحرف الرموز مثل E34 عند عرضها داخل اللعبة.

فرّق بين المتكلم والمخاطب ومن يدور الحديث عنه. حين تعتذر هونغ هوا عن فعلها تقول «أنا آسفة». أما إذا روت اعتذار رجل فصيغة الاعتذار تتبع صاحب الكلام المنقول. راجع اسم المتكلم في بيانات المشهد، وما يسبق العبارة ويلحقها، ثم قارن النص الفرنسي أو الياباني عند الالتباس. قد يحدد شرط المشهد جنس اللاعب بينما يكون المتكلم شخصا آخر. وإذا ورد جواب واحد على لسان البطل والبطلة معا، فاختر صيغة تصلح لكليهما، مثل «لنبدأ» حين يكون المقصود إعلان الاستعداد. وإذا نودي البطل أو البطلة في عبارة مشتركة، جاز «يا أملنا الموعود» لأن وصف «الموعود» يعود على الأمل. راجع ما يلي النداء أيضا، فقد يحتاج فعل الأمر نفسه إلى إعادة صياغة.

حافظ على فصحى واحدة تتسع لاختلاف الأصوات. يستطيع الجندي أن يصيح «اثبتوا!» في المعركة، وتستطيع الأم أن تواسي ابنتها بكلمات يسيرة. وللمنادي في حفل التتويج فسحة في الإيقاع والألقاب. لا تثقل المزاح أو الحزن بالسجع، وأبق مصطلحات زينون التقنية دقيقة حين يشرح صنع الأجساد. عند ترجمة لقب، راجع ظهوره في التعريف بالشخصية وفي سجلها وفي خاتمة قصتها. عزف ليدا هو الذي يسحر سامعيها، ومنه جاء لقبها.

The IGDA Localization SIG recommends giving translators the speaker, addressee and scene context. Ubisoft's Arabic localization team for *Mirage* describes building a shared vocabulary for its setting and reviewing character delivery. Those practices inform the checks above. [IGDA's guide to localization context](https://igda.org/news-archive/high-quality-localization-help-loc-help-you/) · [Ubisoft's account of Arabic localization](https://news.ubisoft.com/en-ca/article/YJZekvzJH1XyKlygfhSbF)

To queue dialogue for bilingual review, run the audit against matching local resources and the Arabic pack in the player being tested. It checks visible first-person predicates and selected recurring mistranslations. A flagged line can be an accurate quotation or a reference to someone else. The report stores IDs and review reasons. Use a fresh filename for each run.

```powershell
python tools/localization/dialogue_review.py --romfs "D:\GameDumps\FortunesWeave\romfs" --reviewed fagrishot/arabic_data --campaign "D:\FagriShot\fagrishot\arabic_data\campaign" --report work/private/localization/dialogue-review.json
```

## Verify an edit

Run `python -m unittest discover -s tests -p test_localization.py -v` with the project's Python environment. It checks MSBT framing, unmodified data, placeholders, binary alternatives and the tutorial graphic's BC7 block encoding using original synthetic fixtures.

Generate a fresh overlay through the player's Arabic preview setting. An incorrect source fingerprint stops installation. Verify the edited message in the running game at the intended text scale. Include both text choices for dynamic labels and an example with punctuation or numbers. The tutorial illustration is redrawn separately against a fingerprinted local texture. All texture and font outputs belong in the private overlay.

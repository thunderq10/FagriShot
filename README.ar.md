# دليل FagriShot على Windows

[English README](README.md)

يتيح لك **FagriShot** بناء نسخة تعمل مباشرةً على Windows x64 من لعبة *Fire Emblem: Fortune’s Weave* الصادرة على Nintendo Switch 2. يتولى **thunderq10** صيانة المشروع. يوفر المستودع الشيفرة وأدوات البناء، وتستخرج أنت ملفات اللعبة من نسختك وتبني منها ملف التشغيل على جهازك.

في اختبار التحديث `131072`، ظهرت رسالة الحفظ التلقائي **Autosave**، ونقل الضغط على **Enter** اللعبة إلى المشهد الافتتاحي. وصلت جلسات الاختبار إلى شاشة عنوان الفصل والحوار وخريطة العالم وقائمة **Casual / Classic** وشاشة إنشاء الشخصية. ما زال تنفيذ أوامر المعارك التكتيكية ودور العدو وحفظ تقدم الحملة ثم تحميله بحاجة إلى اختبار. [تفاصيل التحقق](docs/status.md)

## 1. تجهيز Windows

- استخدم Windows 11 بنواة x64، ومعالجًا يدعم `CMPXCHG16B` و`SSE4.2` و`POPCNT`، وبطاقة رسومية بتعريف يدعم Vulkan 1.3.
- ثبّت Git وPowerShell 7 وPython 3.13 بنسخة x64 وNode.js 22.12 أو أحدث مع npm. جرى الاختبار باستخدام Node.js 26. تأكد من أن الأمر `python` يفتح Python 3.13. تحتاج واجهة الإعداد إلى Microsoft Edge WebView2 Runtime.
- في Visual Studio Installer، اختر **Desktop development with C++**، وأدوات MSVC x64 وWindows SDK و**C++ CMake tools for Windows** لتوفير CMake 3.25 أو أحدث وNinja.
- ثبّت Vulkan SDK وتأكد من إمكانية تشغيل `spirv-val` و`spirv-dis` من PowerShell. تتطلب تهيئة أدوات البناء أول مرة اتصالًا بالإنترنت ومساحة لملفات الترجمة المؤقتة وذاكرة التخزين المؤقت للرسوم.

## 2. تنزيل المصدر وبناء الأدوات

افتح PowerShell 7 في المجلد الذي تريد وضع المشروع فيه، ثم انسخ المستودع وادخل مجلد المصدر.

```powershell
git clone https://github.com/thunderq10/FagriShot.git
Set-Location .\FagriShot
```

نفّذ أوامر البناء التالية بالترتيب من داخل مجلد `FagriShot`. تثبّت هذه الأوامر بيئة Python ومترجم LLVM محليًا، ثم تبني أدوات الرسوم وبرنامج الإعداد.

```powershell
python -I scripts/setup_python.py
.\tools\.venv\Scripts\python.exe -I scripts/setup_llvm.py
python -I scripts/build_shaders.py --rebuild
python -I scripts/build_launcher.py --bootstrap
python -I scripts/fagrishot.py doctor
```

بعد اكتمال البناء ستجد برنامج الإعداد في `work\tools\FagriShotLauncher.exe`. تستطيع متابعة الخطوات التالية من جلسة PowerShell نفسها. [تفاصيل أدوات البناء](docs/BUILDING.md)

## 3. تحديد ملفات اللعبة

جهّز مجلدين منفصلين من ملفات نسختك التي استخرجتها بعد فك تشفيرها. التحديث المطلوب هو `131072` ومعرّف اللعبة `04007EF01F8AE000`. يمكنك الاحتفاظ بالمجلدين خارج المشروع.

- **ExeFS** يحتوي مباشرة على الملفات `main` و`sdk` و`subsdk0` و`subsdk1` و`subsdk2` و`rtld` و`main.npdm`.
- **RomFS** يحتوي على البيانات الفعلية بعد دمج اللعبة الأساسية والتحديث. العدد المسجّل لهذه النسخة هو **252,686 ملفًا**. يفحص FagriShot محتوى الملفات عند تسجيل نسختك المحلية.

افتح `work\tools\FagriShotLauncher.exe`. في **Game setup**، اختر مجلدي **ExeFS** و**RomFS** نفسيهما، ثم اضغط **Save locations**. ويمكنك حفظ المجلدين بالأمر التالي بعد استبدال مساري المثال بمساريك.

```powershell
python -I scripts/fagrishot.py configure --exefs "D:\MyDump\exefs" --romfs "D:\MyDump\romfs"
```

## 4. بناء اللعبة وتسجيل نسختك وتجهيز الرسوم

استخدم المجلدين المحفوظين ونفّذ الخطوات بالترتيب. انتظر نجاح كل أمر قبل الانتقال إلى التالي.

```powershell
python -I scripts/fagrishot.py build-code --jobs 8
python -I scripts/fagrishot.py enroll-local
python -I scripts/fagrishot.py prepare --jobs 8
```

يبني `build-code` وحدات اللعبة الست ويربطها في `work\private\game\FagriShot.exe`. ويمكن تشغيل الخطوة نفسها من زر **Build game code**. بعدها يسجّل `enroll-local` بصمات ملفاتك وإصدار المصدر لتجربة هذه النسخة على جهازك. يفيد هذا التسجيل لأن المشروع المنبع ينشر بصمات الملفات التنفيذية، في حين لا يتضمن سجلّ التوافق العام بصمة مستقلة لكامل RomFS. أخيرًا يحوّل `prepare` برامج التظليل ويتحقق منها ويحفظها للاستخدام عند اللعب. يقابله في الواجهة زر **Prepare shaders**. [كيفية التحقق من الملفات](docs/inputs.md)

## 5. تشغيل اللعبة والتحكم

انقر نقرًا مزدوجًا على **`work\private\game\FagriShot.exe`**. تظهر نافذة تقدم أثناء فحص الملفات وذاكرة التظليل المؤقتة، ثم تُفتح نافذة اللعبة. يقرأ هذا الفحص نحو 29 غيغابايت من RomFS عند كل تشغيل، وقد يستغرق عدة دقائق بحسب سرعة القرص. يمكنك أيضًا بدء اللعبة بالأمر `python -I scripts/fagrishot.py play`. يظل برنامج **`work\tools\FagriShotLauncher.exe`** متاحًا لتغيير المجلدات وإدارة النسخ الاحتياطية.

| الإجراء | المفتاح الافتراضي |
| --- | --- |
| تأكيد رسالة **Autosave** والاختيارات | **Enter** أو **Space** |
| الرجوع | **Esc** أو **Backspace** |
| الحركة | **W A S D** |
| تحريك الكاميرا | مفاتيح الأسهم أو **I J K L** |
| عرض الإعدادات وتعيينات الأزرار | **F1** ثم **Controls** |

## عند تعثّر الإعداد أو التشغيل

- **لم يعثر البرنامج على الملفات.** افتح **Game setup**، وتأكد من وجود `main` داخل مجلد ExeFS المحدد مباشرةً، ثم أعد تحديد المجلدين واضغط **Save locations**. تعرض **Local files → Run diagnostics** المسارات الفعلية والسجلّات.
- **ظهرت رسالة عن اختلاف الإصدار أو غياب تسجيل محلي.** تأكد من أن ExeFS وRomFS يعودان إلى التحديث نفسه وأن عملية الاستخراج اكتملت. إذا غيّرت مجلد الملفات أو محتواه، فأعد تنفيذ خطوات البناء والتسجيل وتجهيز الرسوم في القسم الرابع. يربط التسجيل النسخة بمجلداتها وبإصدار المصدر.
- **توقف البناء أو تجهيز الرسوم.** اقرأ أول خطأ في نافذة الإعداد أو PowerShell. توجد سجلات بناء اللعبة في `work\private\logs\`. عند انقطاع البناء أعد تشغيل الأمر نفسه. إذا أبلغ النظام عن نفاد الذاكرة فاخفض عدد المهام، مثل `--jobs 2`.
- **النسخ الاحتياطي والاستعادة.** أغلق اللعبة، ثم افتح **Local files** واضغط **Back up saves** لإنشاء نسخة. للاستعادة اختر النسخة من **Restore backup** واضغط **Restore**. يتحقق البرنامج من سلامتها ويحفظ نسخة من ملفات الحفظ الحالية قبل استبدالها. يعرض **Local files** موقع الحفظ الفعلي، بما في ذلك عند استخدام Python من Microsoft Store. [دليل الحفظ والاستعادة](docs/storage.md)

## المساهمون والترخيص

يعتمد FagriShot على مشروع نقل *Fortune’s Weave* الذي أنشأه **Goldoire** وطوّره القائمون عليه والمساهمون في فروعه، وعلى عمل **drakolordx7** في **nxrecomp**. يتتبع هذا المشروع مراجعة محددة من فرع **metrist38/fefw-recomp**. تخضع شيفرة FagriShot وشعاره الأصلي لترخيص **GPL-3.0-or-later**، وتبقى حقوق اللعبة ومحتواها لأصحابها. [المشروع المنبع](https://github.com/metrist38/fefw-recomp/tree/ba87770fece1066f736e11e81e36b714ec56df0d) · [الترخيص](LICENSE) · [إشعارات مكتبة التشغيل](runtime/THIRD_PARTY_NOTICES.md) · [إشعارات المترجم](recomp/THIRD_PARTY.md)

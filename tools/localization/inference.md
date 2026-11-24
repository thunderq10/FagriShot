# Offline Arabic translation workspace

These authoring tools translate editable strings from the owner's local US English MSBT catalog. Optional Japanese context comes from matching labels in the same owner's Japanese message tables. Tools keep bilingual text, model weights, SQLite memory, candidate reports and generated overlays under the chosen private workspace. The player reads compressed, source-fingerprinted Arabic tables at setup and has no inference service at runtime.

## Models and environment

The first pass uses the English-to-Arabic Marian model with a beam of three in CTranslate2. A second decoder translates selected source units on a llama.cpp server bound to `127.0.0.1`. A multilingual sentence encoder compares the source and its individual clauses against both candidate translations. Semantic similarity helps rank passages for editing. A score alone cannot establish correct character gender, resolve a pronoun or detect every reversal. [OPUS English to Arabic](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-ar) · [Tencent HY-MT1.5](https://huggingface.co/tencent/HY-MT1.5-7B-GGUF) · [Multilingual E5](https://huggingface.co/intfloat/multilingual-e5-small)

The tested Windows authoring environment used Python 3.12, CTranslate2 4.9.1, Transformers 4.57.3, SentencePiece, tokenizers, zstandard, a CUDA 12 runtime and an RTX 4090 Laptop GPU. `inference_models.py` pins public model revisions and verifies the downloaded Git or LFS object hashes. It accepts only a model name and destination. A GPU machine with existing weights can perform the entire inference and export offline. [CTranslate2 installation](https://opennmt.net/CTranslate2/installation.html)

## Generate and check a draft

Start with the private JSONL catalog created by `fagrishot.localization.write_catalog` as described in the localization README. The following PowerShell example assumes an activated authoring environment, a private catalog and a directory containing the CUDA runtime DLLs.

```powershell
$engine = 'work/private/full-arabic/engine'
$catalog = 'work/private/localization/USen.catalog.jsonl'
python tools/localization/inference_models.py opus "$engine/models/opus-tc-big"
ct2-transformers-converter --model "$engine/models/opus-tc-big" --output_dir "$engine/models/opus-ct2" --quantization float16 --copy_files source.spm target.spm
python tools/localization/inference.py --catalog $catalog --model "$engine/models/opus-ct2" --work $engine --cuda-dir $cudaDirectory
```

The first pass records a hash of the converted model, tokenizers and decoding settings in `opus-profile.json`. Completed batches are saved to `memory.sqlite`. Reusing the same model, settings and private workspace resumes the pass.

Use `inference_repair.py` on mechanically flagged units. It checks quantities, markup, supported characters, repeated text, question marks and gross length mismatches. Its clause decoder and scoped interface glossary can recover dropped segments. `inference_refine.py` accepts a JSON array of authentic pending English units, checks the identity of the model advertised by the local server and saves each completed group in the SQLite memory. A 7B Q6 model worked with 16 parallel slots, a total context of 16,384 tokens, batch size 512 and microbatch size 256. The translation and game rendering tests used the GPU at separate times.

`inference_audit.py` checks the sealed draft against all eligible source records. It reports names, potential negation loss, identifiers, question marks and unusual punctuation by table. `inference_selection.py` runs a second all-table screen for repeated characters, very short multi-sentence output and high-risk semantic matches. `inference_consistency.py` applies editor-approved spelling aliases only when the English segment names the same character. `inference_adjudicate.py` compares a stronger candidate against the previous sentence and its source clauses and writes a separate audit and proposed exact memory. Review the proposed Arabic in context before promoting it. E5 works with `--device cpu` for an independent low-memory review.

`inference_pack.py` takes one or more ordered `--profile` values, local `--terms` memories, optional UI-scoped `--ui-terms` and a `--reviewed` catalog directory. It retains text/control boundaries and choices, checks reviewed source fingerprints, writes independently compressed per-table files into a fresh directory, and reports the exact eligible, reviewed, draft and withheld counts. Use a frozen SQLite backup when selectively promoting later candidates so that unfinished second-pass results cannot enter the package. Keep the bilingual audit private and use the project’s independent source-resource and overlay checks before release.

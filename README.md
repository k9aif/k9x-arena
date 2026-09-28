# K9X Arena

Runs the same task suite on several LLMs, grades every answer, awards 1–5
stars per model per task type, and audits the K9-AIF Intelligent Model
Router: did it send each task to the model that actually did best? It ends
with a recommended `inference.model_catalog` for your solution's config.

**Status:** specification stage. See [SPEC.md](SPEC.md).

**Next step:** feed `SPEC.md` to K9X Studio (Intake → Process Specification)
to generate the K9-AIF scaffold, then implement the graders and the report UI.

Part of the [K9X ecosystem](https://github.com/k9aif/k9x-ecosystem), built on
the [K9-AIF framework](https://github.com/k9aif/k9-aif-framework).

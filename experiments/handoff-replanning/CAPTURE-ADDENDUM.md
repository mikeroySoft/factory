# Source-capture check — resolved; no rerun

During adjudication Main mistook transcript rendering of long paragraphs (768-character display clipping) for clipping of the stored source data. Main announced a corrective rerun and notified the independent reviewers before checking actual string equality.

A direct programmatic comparison then established that ALL NINE original frozen issue bodies equal the complete `gh api` bodies, after trimming outer whitespace, and every issue `updated_at` is unchanged. The 2072-character #56 Scope was intact in `sources.json`; only its displayed rendering was shortened. See `evidence/capture-fidelity.json` for the equality results and complete body hashes.

Therefore the original six runs are the complete-input protocol run. No additional model runs were executed. The temporary optional-input runner change was reverted exactly; the runner again matches the hash recorded by the original run manifest. The hypothesis, source packet, prompts, outputs and decision criterion were not changed.

Independent reviewers were told first about the suspected clipping and then about its correction. Their original reports are retained verbatim; Main rejects the resulting 'collector defect / rerun required' findings with the direct equality evidence. Their other item-level findings and limitations remain applicable. This incident does not manufacture a blind or independent replication: source IDs expose condition membership and Main has prior context, as already declared.

Lesson for this experiment: inspect structured values and hashes rather than infer capture loss from a shortened transcript display.

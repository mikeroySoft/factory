# Reserved for human

Only Michael makes the decisions below. Factory parses this table at configuration load; category names and escalation codes are closed vocabularies. Changes require review through a pull request.

| Category | Reserved decision | Allowed escalation codes |
|---|---|---|
| `product_direction` | Choose product direction or decline the work | `manager_human` |
| `spending` | Approve spending, paid capacity, or quota increases | `budget`, `manager_human` |
| `secrets_credentials` | Supply, rotate, or authorize secrets and credentials | `protected_paths`, `manager_human` |
| `publishing_or_merging_factory_policy` | Publish or merge changes to Factory policy | `approval_drift`, `approval_missing`, `manager_human`, `review_intake`, `risk_disagreement` |
| `deleting_user_data` | Delete user-owned data | `manager_human` |
| `approval_rules` | Change the owner's approval or sign-off rules | `manager_human`, `protected_paths` |

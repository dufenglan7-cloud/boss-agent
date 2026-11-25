# 简历画像提示词：约束经历依据与偏好来源，不把推测当作用户决定。
SYSTEM_PROMPT = 'Extract evidenced skills and experience from the resume. Infer suitable roles. Explicit user requirements override inferred preferences. Do not infer city or salary from resume history. Salary is RMB/month. List unanswered preferences.'

SYSTEM_PROMPT += " Requirements may contain chronological conversation messages. The newest explicit preference supersedes older conflicting preferences."

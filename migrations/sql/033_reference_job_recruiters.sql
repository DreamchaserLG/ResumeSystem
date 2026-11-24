WITH recruitment_alignment(title,recruiter_department) AS (
    VALUES
      ('CTO/CIO/技术VP','信息技术部'),
      ('VIP 客服专员（双休）','百货事业部'),
      ('企划经理/总监','百货事业部'),
      ('商业招商经理','百货事业部'),
      ('商业运营经理','百货事业部'),
      ('销售专员','百货事业部'),
      ('销售经理/总监（江苏省内任意城市）','百货事业部')
), known_recruiter AS (
    SELECT d.name,min(u.id) AS user_id
    FROM department d JOIN department_admin da ON da.department_id=d.id
    JOIN app_user u ON u.id=da.user_id AND u.status='active'
    GROUP BY d.name HAVING count(*)=1
)
UPDATE job j SET recruiter_user_id=owner.user_id
FROM recruitment_alignment align
JOIN known_recruiter owner ON owner.name=align.recruiter_department
JOIN department d ON d.name='新百总部'
WHERE j.department_id=d.id AND j.title=align.title AND j.recruiter_user_id IS NULL
  AND btrim(j.recruiter_name)=''
  AND EXISTS (SELECT 1 FROM job_posting_reference ref WHERE ref.job_id=j.id);

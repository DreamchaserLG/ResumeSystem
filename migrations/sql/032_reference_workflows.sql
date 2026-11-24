CREATE TEMP TABLE reference_workflow (
    department_name text,
    title text,
    rounds text[] NOT NULL,
    PRIMARY KEY (department_name,title)
) ON COMMIT DROP;

INSERT INTO reference_workflow VALUES
('大健康医疗部','中医科医师',ARRAY['中医辨证与病例讨论','科室面谈']),
('大健康医疗部','医保处专员',ARRAY['医保结算实务测评','部门面谈']),
('大健康医疗部','病理科学科带头人',ARRAY['疑难病理案例评审','学科建设方案答辩','院级面谈']),
('大健康医疗部','综合内科医师',ARRAY['内科病例研判','科室面谈']),
('大健康医疗部','董事长业务助理（医院方向）',ARRAY['医院运营案例分析','用人部门面谈','管理层面谈']),
('大健康医疗部','麻醉医师',ARRAY['麻醉病例讨论','围术期团队协作面谈','科室面谈']),
('新百总部','CTO/CIO/技术VP',ARRAY['技术架构评审','数字化方案答辩','管理层面谈']),
('新百总部','HRD（外派海外）',ARRAY['海外组织管理案例','跨文化沟通面谈','管理层面谈']),
('新百总部','VIP 客服专员（双休）',ARRAY['会员服务情景模拟','门店主管面谈']),
('新百总部','企划经理/总监',ARRAY['营销作品评审','商场企划方案答辩','业务负责人面谈']),
('新百总部','商业招商经理',ARRAY['品牌招商案例','商务谈判模拟','项目负责人面谈']),
('新百总部','商业运营经理',ARRAY['商场运营案例','经营数据测评','业务负责人面谈']),
('新百总部','多元化集团公司总部接待专员',ARRAY['商务接待情景模拟','行政主管面谈']),
('新百总部','投资总监/董事长投资助理',ARRAY['投资案例测评','尽调与估值方案答辩','管理层面谈']),
('新百总部','新动力管培生',ARRAY['综合能力测评','小组任务','综合面谈']),
('新百总部','销售专员',ARRAY['客户沟通情景模拟','销售主管面谈']),
('新百总部','销售经理/总监（江苏省内任意城市）',ARRAY['区域销售方案答辩','渠道与团队管理面谈']),
('法务部','法务管培生',ARRAY['合同审查笔试','法律案例讨论','法务负责人面谈']);

CREATE TEMP TABLE reference_workflow_target ON COMMIT DROP AS
SELECT j.id,w.rounds
FROM reference_workflow w JOIN department d ON d.name=w.department_name
JOIN job j ON j.department_id=d.id AND j.title=w.title
JOIN job_posting_reference ref ON ref.job_id=j.id
WHERE (SELECT array_agg(r.name ORDER BY r.position) FROM job_interview_round r WHERE r.job_id=j.id)
      = ARRAY['一面','二面','终面']
  AND NOT EXISTS (SELECT 1 FROM application a WHERE a.job_id=j.id AND a.interview_round_id IS NOT NULL);

UPDATE job_interview_round r
SET name=t.rounds[r.position]
FROM reference_workflow_target t
WHERE r.job_id=t.id AND r.position<=cardinality(t.rounds);

DELETE FROM job_interview_round r USING reference_workflow_target t
WHERE r.job_id=t.id AND r.position>cardinality(t.rounds);

-- 003_seed.sql · 种子数据：演示部门/两级账号/字段定义与策略/词典（幂等：ON CONFLICT 跳过）

-- ========= 部门 =========
INSERT INTO department (id, name) OVERRIDING SYSTEM VALUE VALUES
    (1, '技术研发部'), (2, '人力资源部'), (3, '市场部'), (4, '财务部')
ON CONFLICT (id) DO NOTHING;
SELECT setval('department_id_seq', (SELECT max(id) FROM department));

-- ========= 两级账号（密码均为 PBKDF2-SHA256，60000 轮，盐 'rs-v1'）=========
-- admin / admin123（超级管理员）；hr_tech / hr123456（技术研发部 HR）；hr_market / dev123456（市场部 HR）
INSERT INTO app_user (id, display_name, email, password_hash) OVERRIDING SYSTEM VALUE VALUES
    (1, '系统管理员', 'admin@company.internal', 'a58e2cf44d94d802b38d4324244008eb2c576de5bad9ff0abed1ce41cff7a1dc'),
    (2, '张工（技术研发部 HR）', 'hr.tech@company.internal', 'cbaf558d7dfdcb992a8c35b992273d94457c4ff99535694e0ea7754c2752ab37'),
    (3, '李莉（市场部 HR）', 'hr.market@company.internal', 'b0bc000640f55044ea1b5d5da26a9484c27a5297e2e5c8e36900d0d5a576d2f3')
ON CONFLICT (id) DO NOTHING;
SELECT setval('app_user_id_seq', (SELECT max(id) FROM app_user));

INSERT INTO system_admin (user_id, granted_by) VALUES (1, 1)
ON CONFLICT (user_id) DO NOTHING;
INSERT INTO department_admin (department_id, user_id, granted_by) VALUES (1, 2, 1), (3, 3, 1)
ON CONFLICT (department_id, user_id) DO NOTHING;

-- ========= 演示岗位 =========
INSERT INTO job (id, department_id, title, jd, status) OVERRIDING SYSTEM VALUE VALUES
    (1, 1, '高级后端工程师（Go）', '负责订单与交易中台研发，要求 3 年以上后端经验，熟悉 Go 与 Kubernetes。', 'open'),
    (2, 1, '前端工程师', '负责 HR 内部系统与候选人门户的前端开发，熟悉 React 或 Vue。', 'open'),
    (3, 3, '品牌市场经理', '负责品牌活动策划与执行，3 年以上市场经验。', 'open')
ON CONFLICT (id) DO NOTHING;
SELECT setval('job_id_seq', (SELECT max(id) FROM job));

-- ========= 字段定义（核心集，长尾后续按 04 §5 演进）=========
INSERT INTO field_definition (field_key, display_name, value_type, description) VALUES
    ('profile.name',         '姓名',           'text',     '候选人姓名'),
    ('profile.city',         '现居城市',       'location', '当前所在城市'),
    ('profile.photo_present','版面含照片',     'boolean',  '简历版面是否检测到头像/证件照区域'),
    ('profile.summary',      '个人总结',       'text',     '求职意向或个人概述'),
    ('contact.phone',        '联系电话',       'text',     NULL),
    ('contact.email',        '邮箱',           'text',     NULL),
    ('work.company',         '公司',           'organization', NULL),
    ('work.title',           '岗位',           'text',     NULL),
    ('work.employment_type', '雇佣类型',       'enum',     '全职/实习/兼职等'),
    ('work.period',          '工作起止',       'period',   NULL),
    ('work.description',     '工作描述',       'text',     NULL),
    ('education.school_raw', '学校（原写法）', 'organization', NULL),
    ('education.degree_level','学历层次',      'enum',     'phd/master/bachelor/associate/high_school/other'),
    ('education.major',      '专业',           'text',     NULL),
    ('project.project_name', '项目名称',       'text',     NULL),
    ('project.role',         '担任角色',       'text',     NULL),
    ('skill.name',           '技能',           'text',     '归一化技能名')
ON CONFLICT (field_key) DO NOTHING;

-- 字段别名（抽取归一化用）
INSERT INTO field_alias (alias_normalized, field_key) VALUES
    ('现居地', 'profile.city'), ('所在城市', 'profile.city'), ('居住城市', 'profile.city'),
    ('期望职业', 'profile.summary'), ('求职意向', 'profile.summary'),
    ('现居', 'profile.city'), ('手机', 'contact.phone'), ('电话', 'contact.phone'),
    ('邮箱', 'contact.email'), ('email', 'contact.email'),
    ('公司名称', 'work.company'), ('工作单位', 'work.company'),
    ('职位', 'work.title'), ('岗位', 'work.title'),
    ('毕业院校', 'education.school_raw'), ('学校', 'education.school_raw'),
    ('专业', 'education.major'), ('学历', 'education.degree_level'),
    ('工作性质', 'work.employment_type')
ON CONFLICT (alias_normalized) DO NOTHING;

-- ========= 字段使用策略（default-deny，v1 初始白名单；policy_version=1）=========
-- preview：HR 复核界面必需的结构化字段
INSERT INTO field_usage_policy (field_key, purpose, allowed, reason, policy_version) VALUES
    ('profile.name',          'preview', true,  'HR 复核必需', 1),
    ('profile.city',          'preview', true,  'HR 复核必需', 1),
    ('profile.photo_present', 'preview', true,  '有/无头像分层展示', 1),
    ('profile.summary',       'preview', true,  'HR 复核必需', 1),
    ('contact.phone',         'preview', false, '联系方式默认不出现在列表预览', 1),
    ('contact.email',         'preview', false, '联系方式默认不出现在列表预览', 1),
    ('work.company',          'preview', true,  'HR 复核必需', 1),
    ('work.title',            'preview', true,  'HR 复核必需', 1),
    ('work.period',           'preview', true,  'HR 复核必需', 1),
    ('education.school_raw',  'preview', true,  'HR 复核必需', 1),
    ('education.degree_level','preview', true,  'HR 复核必需', 1)
ON CONFLICT (field_key, purpose, policy_version) DO NOTHING;

-- filter：仅类型明确、合规允许的字段（photo_present 明确 deny——照片不参与筛选）
INSERT INTO field_usage_policy (field_key, purpose, allowed, reason, policy_version) VALUES
    ('education.degree_level', 'filter', true,  '硬条件筛选', 1),
    ('work.period',            'filter', true,  '工作年限计算', 1),
    ('profile.photo_present',  'filter', false, '照片不作为筛选输入（合规红线）', 1)
ON CONFLICT (field_key, purpose, policy_version) DO NOTHING;

-- search：可进入关键词文本投影的内容
INSERT INTO field_usage_policy (field_key, purpose, allowed, reason, policy_version) VALUES
    ('work.description',   'search', true, '经历文本检索', 1),
    ('profile.summary',    'search', true, '求职意向检索', 1),
    ('project.project_name','search', true, '项目检索', 1),
    ('skill.name',         'search', true, '技能上下文检索', 1),
    ('contact.phone',      'search', false, '联系方式不进检索（合规红线）', 1),
    ('contact.email',      'search', false, '联系方式不进检索（合规红线）', 1)
ON CONFLICT (field_key, purpose, policy_version) DO NOTHING;

-- export：集成账号可读取的字段（配合 integration_client.allowed_fields 交集）
INSERT INTO field_usage_policy (field_key, purpose, allowed, reason, policy_version) VALUES
    ('profile.name',           'export', true,  '结构化结果契约', 1),
    ('profile.city',           'export', true,  '结构化结果契约', 1),
    ('profile.photo_present',  'export', false, '照片信息不出 API', 1),
    ('work.company',           'export', true,  '结构化结果契约', 1),
    ('work.title',             'export', true,  '结构化结果契约', 1),
    ('work.period',            'export', true,  '结构化结果契约', 1),
    ('education.school_raw',   'export', true,  '结构化结果契约', 1),
    ('education.degree_level', 'export', true,  '结构化结果契约', 1)
ON CONFLICT (field_key, purpose, policy_version) DO NOTHING;

-- ========= 技能词典 v0 =========
INSERT INTO skill (id, name) OVERRIDING SYSTEM VALUE VALUES
    (1,'Go'),(2,'Java'),(3,'Python'),(4,'JavaScript'),(5,'TypeScript'),(6,'Kubernetes'),
    (7,'Docker'),(8,'React'),(9,'Vue'),(10,'PostgreSQL'),(11,'MySQL'),(12,'Redis'),
    (13,'Kafka'),(14,'gRPC'),(15,'TensorFlow'),(16,'PyTorch'),(17,'Spark'),(18,'Hadoop'),
    (19,'Linux'),(20,'C++'),(21,'Spring'),(22,'微服务'),(23,'高并发'),(24,'机器学习')
ON CONFLICT (id) DO NOTHING;
SELECT setval('skill_id_seq', (SELECT max(id) FROM skill));

INSERT INTO skill_alias (alias_normalized, skill_id) VALUES
    ('golang',1),('go语言',1),('go 语言',1),
    ('python3',3),('py',3),
    ('js',4),('javascript',4),('es6',4),
    ('ts',5),('typescript',5),
    ('k8s',6),('k8',6),
    ('pg',10),('postgres',10),('postgresql',10),
    ('mysql',11),
    ('hadoop生态',18),('hadoop ecosystem',18),
    ('spark',17),('pyspark',17),
    ('tf',15),('tensorflow',15),
    ('torch',16),('pytorch',16),
    ('高并发系统',23),('高并发架构',23),
    ('机器学习算法',24),('ml',24)
ON CONFLICT (alias_normalized) DO NOTHING;

-- ========= 学校词典 v0（示例子集，生产按教育部名单全量导入）=========
INSERT INTO school (id, canonical_name) OVERRIDING SYSTEM VALUE VALUES
    (1,'清华大学'),(2,'北京大学'),(3,'浙江大学'),(4,'复旦大学'),(5,'上海交通大学'),
    (6,'南京大学'),(7,'中国科学技术大学'),(8,'华中科技大学'),(9,'武汉大学'),(10,'杭州电子科技大学')
ON CONFLICT (id) DO NOTHING;
SELECT setval('school_id_seq', (SELECT max(id) FROM school));

INSERT INTO school_alias (alias_normalized, school_id) VALUES
    ('清华',1),('tsinghua',1),('清华大学',1),
    ('北大',2),('peking university',2),
    ('浙大',3),('zhejiang university',3),
    ('复旦',4),('fudan university',4),
    ('上海交大',5),('sjtu',5),
    ('南大',6),('nanjing univ',6),
    ('中科大',7),('ustc',7),
    ('华科',8),('hust',8),
    ('武大',9),('whu',9),
    ('杭电',10),('hdu',10)
ON CONFLICT (alias_normalized, school_id) DO NOTHING;

-- 学校标签：可验证布尔事实，来源可溯源（示例：第二轮双一流名单，2022）
INSERT INTO school_tag (school_id, tag_code, scope, subject, list_version, source) VALUES
    (1,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (1,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (1,'is_211','school',NULL,'官方名单','教育部公开名单'),
    (2,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (2,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (2,'is_211','school',NULL,'官方名单','教育部公开名单'),
    (3,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (3,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (3,'is_211','school',NULL,'官方名单','教育部公开名单'),
    (4,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (4,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (5,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (5,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (6,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (6,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (7,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (7,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (8,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (8,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (9,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (9,'is_985','school',NULL,'官方名单','教育部公开名单'),
    (10,'is_double_first_class','school',NULL,'2022-第二轮','教育部公开名单'),
    (10,'is_211','school',NULL,'官方名单','教育部公开名单')
ON CONFLICT (school_id, tag_code, scope, subject, list_version) DO NOTHING;

-- ========= 检索同义词词典 v0（对应 06 §1 查询扩展）=========
INSERT INTO search_term_group (id, dict_version, section_type) OVERRIDING SYSTEM VALUE VALUES
    (1, 1, 'work'),    -- 高并发组
    (2, 1, 'work'),    -- 带团队组
    (3, 1, NULL),      -- 微服务组
    (4, 1, 'work'),    -- 大数据组
    (5, 1, 'project')  -- 项目交付组
ON CONFLICT (id, dict_version) DO NOTHING;
SELECT setval('search_term_group_id_seq', (SELECT max(id) FROM search_term_group));

INSERT INTO search_term (group_id, term_norm, is_primary) VALUES
    (1,'高并发',true),(1,'qps',false),(1,'吞吐量',false),(1,'每秒处理',false),(1,'峰值流量',false),
    (2,'带团队',true),(2,'带队',false),(2,'小组负责人',false),(2,'代码评审',false),(2,'mentor',false),(2,'指导新人',false),
    (3,'微服务',true),(3,'服务拆分',false),(3,'service mesh',false),
    (4,'大数据',true),(4,'hadoop',false),(4,'spark',false),(4,'数据仓库',false),(4,'etl',false),
    (5,'从0到1',true),(5,'独立完成',false),(5,'负责搭建',false)
ON CONFLICT (group_id, term_norm) DO NOTHING;

-- ========= 集成账号示例 =========
INSERT INTO integration_client (id, name, status, allowed_fields) OVERRIDING SYSTEM VALUE VALUES
    (1, '集团 HR 数据平台', 'active', ARRAY['profile.name','profile.city','work.company','work.title','work.period','education.school_raw','education.degree_level'])
ON CONFLICT (id) DO NOTHING;
SELECT setval('integration_client_id_seq', (SELECT max(id) FROM integration_client));

INSERT INTO integration_job (client_id, job_id) VALUES (1, 1), (1, 2)
ON CONFLICT (client_id, job_id) DO NOTHING;

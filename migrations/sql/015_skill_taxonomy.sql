-- Card-level capabilities are curated labels. The detailed skill/evidence model stays intact.
CREATE TABLE capability_group (
    code text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_]+$'),
    name text NOT NULL UNIQUE,
    domain text NOT NULL CHECK (domain IN ('technology','people','biomed','business','general')),
    display_order smallint NOT NULL
);
ALTER TABLE capability_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE capability_group FORCE ROW LEVEL SECURITY;
CREATE POLICY capability_read ON capability_group FOR SELECT TO rs_app USING (true);
GRANT SELECT ON capability_group TO rs_app;

ALTER TABLE skill ADD COLUMN capability_code text REFERENCES capability_group(code);
ALTER TABLE skill ADD COLUMN is_programming_language boolean NOT NULL DEFAULT false;
ALTER TABLE resume_skill ADD COLUMN language_priority smallint CHECK (language_priority BETWEEN 1 AND 3);

INSERT INTO capability_group VALUES
    ('database','数据库','technology',10),
    ('deployment','服务器部署','technology',20),
    ('backend','后端开发','technology',30),
    ('frontend','前端开发','technology',40),
    ('distributed','分布式系统','technology',50),
    ('data_engineering','数据工程','technology',60),
    ('machine_learning','机器学习与 AI','technology',70),
    ('testing','软件测试','technology',80),
    ('recruiting','招聘与人才甄选','people',10),
    ('employee_relations','员工关系','people',20),
    ('total_rewards','薪酬绩效','people',30),
    ('talent_development','组织与人才发展','people',40),
    ('people_operations','人力运营','people',50),
    ('drug_discovery','药物研发','biomed',10),
    ('clinical_research','临床研究','biomed',20),
    ('lab_methods','实验技术','biomed',30),
    ('biomanufacturing','生物工艺','biomed',40),
    ('regulatory_quality','质量与法规','biomed',50),
    ('legal_compliance','法务合规','business',10),
    ('finance_accounting','财务与风控','business',20),
    ('business_operations','业务运营','business',30),
    ('analytics','数据分析','general',90),
    ('project_delivery','项目管理','general',100);

WITH mapped(code,names) AS (VALUES
    ('database',ARRAY['PostgreSQL','MySQL','Redis','SQLite','Oracle Database','SQL Server','MongoDB','MariaDB','数据库设计']),
    ('deployment',ARRAY['Docker','Linux','Kubernetes','Nginx','Jenkins','Terraform','Ansible','CI/CD','Helm','服务器部署']),
    ('backend',ARRAY['Spring','FastAPI','Django','Flask','Node.js','MyBatis','REST API','Gin','后端开发']),
    ('frontend',ARRAY['React','Vue','Angular','Vite','HTML','CSS','前端开发']),
    ('distributed',ARRAY['Kafka','gRPC','微服务','高并发','RabbitMQ','分布式系统']),
    ('data_engineering',ARRAY['Spark','Hadoop','Flink','ETL','数据仓库']),
    ('machine_learning',ARRAY['TensorFlow','PyTorch','机器学习','模型训练','计算机视觉','自然语言处理','RAG']),
    ('testing',ARRAY['pytest','Selenium','接口测试','性能测试','自动化测试']),
    ('recruiting',ARRAY['人才甄选','校园招聘','社会招聘','招聘渠道','简历筛选','结构化面试','人才画像']),
    ('employee_relations',ARRAY['员工关系','劳动合同','劳动争议','劳动仲裁','员工沟通']),
    ('total_rewards',ARRAY['薪酬体系','绩效管理','薪酬核算','岗位定级','奖金方案']),
    ('talent_development',ARRAY['人才发展','人才盘点','组织发展','培训体系','继任计划','胜任力模型']),
    ('people_operations',ARRAY['人事运营','人力资源系统','员工入离职','考勤管理','人事数据']),
    ('drug_discovery',ARRAY['药物筛选','靶点验证','药效评价','药物研发','药代动力学','化合物筛选']),
    ('clinical_research',ARRAY['临床研究','临床试验','临床监查','受试者招募','临床数据管理','药物警戒']),
    ('lab_methods',ARRAY['qPCR','PCR','ELISA','流式细胞术','细胞培养','HPLC','蛋白纯化','Western Blot','样本管理']),
    ('biomanufacturing',ARRAY['发酵工艺','工艺放大','工艺验证','生物工艺','制剂工艺']),
    ('regulatory_quality',ARRAY['GMP','GCP','GLP','质量管理','质量审计','注册申报','偏差调查']),
    ('legal_compliance',ARRAY['合同审查','合规审查','诉讼管理','知识产权']),
    ('finance_accounting',ARRAY['财务分析','会计核算','风险控制','预算管理']),
    ('business_operations',ARRAY['用户运营','渠道运营','内容运营','零售运营','市场调研']),
    ('analytics',ARRAY['统计分析','数据可视化','Power BI','Tableau','数据分析']),
    ('project_delivery',ARRAY['项目管理','需求管理','交付管理'])
)
INSERT INTO skill(name,capability_code)
SELECT name,code FROM mapped CROSS JOIN LATERAL unnest(names) AS n(name)
ON CONFLICT (name) DO UPDATE SET capability_code=EXCLUDED.capability_code;

INSERT INTO skill(name,is_programming_language)
SELECT name,true FROM unnest(ARRAY['Go','Java','Python','JavaScript','TypeScript','C++','C#','Rust','Kotlin','Swift',
    'Ruby','PHP','Scala','R','C','Dart','MATLAB','Julia']) AS n(name)
ON CONFLICT (name) DO UPDATE SET is_programming_language=true;

INSERT INTO skill_alias(alias_normalized,skill_id)
SELECT v.alias,s.id FROM (VALUES
    ('sqlite3','SQLite'),('sqlserver','SQL Server'),('mssql','SQL Server'),('oracle db','Oracle Database'),
    ('k8s','Kubernetes'),('spring boot','Spring'),('springboot','Spring'),('nodejs','Node.js'),
    ('vue.js','Vue'),('vuejs','Vue'),('vue3','Vue'),('react.js','React'),('reactjs','React'),('mybatis-plus','MyBatis'),
    ('ci / cd','CI/CD'),('flask框架','Flask'),('自然语言处理(nlp)','自然语言处理'),('nlp','自然语言处理'),
    ('深度学习','机器学习'),('llm','机器学习'),('csharp','C#'),
    ('招聘与配置','人才甄选'),('人才招聘','人才甄选'),('招聘管理','人才甄选'),('社招','社会招聘'),('校招','校园招聘'),
    ('绩效考核','绩效管理'),('薪酬管理','薪酬体系'),('hris','人力资源系统'),
    ('人才培养','人才发展'),('学习发展','培训体系'),
    ('实时荧光定量pcr','qPCR'),('rt-qpcr','qPCR'),('高效液相色谱','HPLC'),('wb实验','Western Blot'),
    ('临床监查员','临床监查'),('pv药物警戒','药物警戒'),('质量体系','质量管理'),
    ('药品注册','注册申报'),('注册资料','注册申报'),('商业数据分析','数据分析')
) v(alias,name) JOIN skill s ON s.name=v.name ON CONFLICT DO NOTHING;

UPDATE search_state SET search_revision=search_revision+1 WHERE singleton;

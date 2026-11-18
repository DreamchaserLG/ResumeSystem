-- Vocabulary for cross-industry screening and the job preview.
INSERT INTO capability_group(code,name,domain,display_order)
VALUES ('logistics','物流与供应链','business',40) ON CONFLICT DO NOTHING;
INSERT INTO skill(name,capability_code) VALUES
 ('Git','deployment'),('SQL','database'),('Excel','analytics'),
 ('法律检索','legal_compliance'),('合同管理','legal_compliance'),('GSP','regulatory_quality'),
 ('WMS','logistics'),('仓储管理','logistics'),('库存管理','logistics'),('进出口报关','logistics'),('供应链管理','logistics')
ON CONFLICT (name) DO NOTHING;
INSERT INTO skill_alias(alias_normalized,skill_id)
SELECT v.alias,s.id FROM (VALUES
 ('法律研究','法律检索'),('合同履约','合同管理'),
 ('仓库管理','仓储管理'),('库存盘点','库存管理'),('报关','进出口报关'),
 ('仓储管理系统','WMS'),('microsoft excel','Excel')
) v(alias,name) JOIN skill s ON s.name=v.name ON CONFLICT DO NOTHING;
UPDATE search_state SET search_revision=search_revision+1 WHERE singleton;

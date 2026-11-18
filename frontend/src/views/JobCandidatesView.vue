<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowBackOutline, DocumentTextOutline, SparklesOutline } from '@vicons/ionicons5'
import { ElButton, ElPagination, ElProgress, ElSelect, ElOption, ElTag, ElTooltip } from 'element-plus'
import { api } from '@/api/client'
import { jobStatusLabel, label, schoolTagLabels, type JobCandidatePage, type ResumeCard } from '@/api/hr'

type CandidateRow = JobCandidatePage['items'][number]
type MatchDimension = { key:string; label:string; score:number; weight:number; color:string; pale:string; evidence:string[] }
const route=useRoute()
const router=useRouter()
const data=ref<JobCandidatePage>()
const page=ref(1)
const size=ref(20)
const loading=ref(false)
const error=ref('')
const selectedId=ref<number>()
const jobId=computed(()=>String(route.params.jobId || ''))
let requestSequence=0
const demo=ref(false)
async function load() {
  const seq=++requestSequence
  error.value=''; loading.value=true
  try {
    const result=await api.get<JobCandidatePage>('/jobs/'+encodeURIComponent(jobId.value)+'/candidates?'+new URLSearchParams({page:String(page.value),size:String(size.value)}))
    if(seq!==requestSequence) return
    demo.value=false; data.value=result
    if(!result.items.some(item=>item.id===selectedId.value)) selectedId.value=result.items[0]?.id
  } catch { if(seq===requestSequence){error.value='';demo.value=true;data.value=buildMock(jobId.value,page.value,size.value);if(!data.value.items.some(item=>item.id===selectedId.value)) selectedId.value=data.value.items[0]?.id} }
  finally {if(seq===requestSequence)loading.value=false}
}
watch([jobId,page,size],([id],previous)=>{
  if(id!==previous?.[0]) {data.value=undefined;if(page.value!==1){page.value=1;return}}
  void load()
},{immediate:true})
const selected=computed<CandidateRow|undefined>(()=>data.value?.items.find(item=>item.id===selectedId.value) || data.value?.items[0])
function degree(tags:ResumeCard['tags']) {const tag=tags.find(tag=>tag.type==='degree');return tag?label(tag.value):'学历待核实'}
function schoolTags(tags:ResumeCard['tags']) {return tags.filter(tag=>tag.type==='school' && schoolTagLabels[tag.value]).map(tag=>schoolTagLabels[tag.value])}
function experience(tags:ResumeCard['tags']) {
  const tag=tags.find(tag=>tag.type==='experience'); if (!tag) return '经验待核实'
  const months=Number(tag.value); if(!Number.isFinite(months)) return '经验待核实'
  return [months>=12?Math.floor(months/12)+' 年':'',months%12?months%12+' 个月':''].filter(Boolean).join(' ') || '不足 1 个月'
}
function selectCandidate(row:CandidateRow) { selectedId.value=row.id }
function openProfile(id:number) { void router.push({name:'applications',query:{job:String(jobId.value),candidate:String(id)}}) }

/* —— 演示数据：后端未接入或岗位无数据时，用确定性伪随机生成版式预览内容 —— */
const surnamePool='王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗郑梁谢宋唐许韩冯邓曹彭'.split('')
const givenPool=['泽宇','思远','嘉树','一鸣','欣然','书航','孟杰','云帆','晓彤','语桐','铭轩','浩然','子墨','雨泽','若曦','书瑶','志强','紫萱','明哲','俊驰']
const companyPool=['字节跳动','腾讯','阿里云','美团','快手','京东','网易','华为','百度','小米','拼多多','微软中国','商汤科技','科大讯飞']
const cityPool=['北京','上海','深圳','杭州','广州','成都','南京','武汉','西安','苏州']
const notePool=['已电话初筛，候选人意向明确','等待技术面安排','简历两段经历重叠，需核实','薪资期望偏高，暂挂起','推荐进入下一轮','联系方式已确认，约下周面试','用人部门更看重项目经验，待评估','两轮面试均已通过，等待终面','近期跳槽较频繁，风险待评估','候选人在职，考虑机会中']
const degreePool=['bachelor','bachelor','master','master','phd','associate']
const schoolPool:Record<string,string[]>={is_985:['清华大学','北京大学','浙江大学','复旦大学'],is_211:['北京邮电大学','西安电子科技大学','南京理工大学','河海大学'],is_double_first_class:['南方科技大学','宁波大学'],study_abroad:['帝国理工学院','墨尔本大学'],qs_top100:['香港大学','多伦多大学']}
const skillPool=['Java','Spring Boot','Kubernetes','Kafka','Redis','MySQL','分布式系统','微服务架构','Docker','CI/CD','PostgreSQL','Elasticsearch']
const rolePool=['高级后端开发工程师','资深 Java 工程师','平台架构师','数据平台工程师','算法工程师']
function buildMock(jobKey:string,pageNum:number,pageSize:number):JobCandidatePage {
  let seed=7
  for(const ch of jobKey) seed=(seed*31+ch.charCodeAt(0))%2147483647
  const rand=()=>{seed=seed*16807%2147483647;return (seed-1)/2147483646}
  const pick=<T,>(pool:T[]):T=>pool[Math.floor(rand()*pool.length)]
  const total=24+Math.floor(rand()*26) /* 每个岗位 24–49 条，不超过 50 */
  const skills=[...new Set(Array.from({length:5},()=>pick(skillPool)))]
  const job={id:Number(jobKey)||0,title:pick(rolePool)+'（演示）',status:'open',department_name:'技术研发部',
    requirements:'负责核心交易链路的设计与演进，主导高并发场景下的稳定性建设，参与团队技术方案评审与新人培养。',
    required_skills:skills,experience_tags:['5–10年'],recruitment_start:'2026-11-01',recruitment_end:'2026-12-31',target_date:'2026-12-31'}
  const items:JobCandidatePage['items']=Array.from({length:total},(_,index)=>{
    const tags:ResumeCard['tags']=[{id:'degree-'+index,type:'degree',value:pick(degreePool),filterable:true}]
    if(rand()<=.85) tags.push({id:'school-'+index,type:'school',value:pick(Object.keys(schoolPool)),filterable:true})
    tags.push({id:'exp-'+index,type:'experience',value:String(48+Math.floor(rand()*144)),filterable:true})
    return {id:(Number(jobKey)||index+1)*1000+index+1,version_id:null,job_id:job.id,job_title:job.title,name:pick(surnamePool)+pick(givenPool),
      target_role:pick(rolePool),gender:null,city:pick(cityPool),address:null,phone:null,status:'active',
      stage:{code:'review' as const,label:'待筛选',round_id:null},workflow_version:1,parse_status:'published',
      page_count:1+Math.floor(rand()*2),has_avatar:rand()>.4,tags,latest_note:rand()>.28?pick(notePool):null,
      salary_midpoint:null,recent_company:null,current_company:pick(companyPool),match_percent:42+Math.floor(rand()*56)}
  }).sort((a,b)=>(b.match_percent??0)-(a.match_percent??0))
  return {job,items:items.slice((pageNum-1)*pageSize,pageNum*pageSize),total,page:pageNum,size:pageSize,school_filters:false,matched_requirements:skills.length}
}

/* 当前仅用于报告版式预览；算法接入后替换为服务端的可解释评分结果。 */
const dimensions:MatchDimension[]=[
  {key:'skill',label:'技能',score:85,weight:.40,color:'#4F81BD',pale:'#DCE6F1',evidence:['Java / Spring Boot','Kafka、Redis 等中间件']},
  {key:'project',label:'项目',score:72,weight:.25,color:'#9BBB59',pale:'#E2EFD9',evidence:['主导微服务迁移项目','高并发订单场景']},
  {key:'capability',label:'能力',score:68,weight:.20,color:'#F79646',pale:'#FDE9D9',evidence:['架构设计与跨团队协作','技术方案评审']},
  {key:'experience',label:'经历',score:60,weight:.15,color:'#8064A2',pale:'#E4DFEC',evidence:['5 年后端研发经验']},
]
const finalScore=50.4, baseScore=74.6, penaltyFactor=.675, penaltyLoss=.325
const penaltyLossAmount=(baseScore*(1-penaltyFactor)).toFixed(1)
const hoverPenalty=ref<string>()
const penalties=[
  {label:'工作年限',detail:'要求 ≥6 年 / 演示值 5 年',severity:'轻微不足',factor:.90,linked:'experience'},
  {label:'必备技能 Kubernetes',detail:'未命中',severity:'中度不足',factor:.75,linked:'skill'},
]
function point(radius:number,angle:number) {const radians=(angle-90)*Math.PI/180;return {x:120+radius*Math.cos(radians),y:120+radius*Math.sin(radians)}}
function arc(radius:number,start:number,end:number) {const first=point(radius,start),last=point(radius,end);return `M ${first.x.toFixed(2)} ${first.y.toFixed(2)} A ${radius} ${radius} 0 ${end-start>180?1:0} 1 ${last.x.toFixed(2)} ${last.y.toFixed(2)}`}
const chartDimensions=computed(()=>{let start=0;return dimensions.map(item=>{const span=item.weight*360;const result={...item,start,end:start+span,fillEnd:start+span*item.score/100,labelPosition:point(102,start+span/2)};start+=span;return result})})
const finalColor=computed(()=>finalScore>=80?'#4CAF50':finalScore>=60?'#FFC107':finalScore>=40?'#FF9800':'#F44336')
const profileName=computed(()=>selected.value?.name || `申请 #${selected.value?.id || '—'}`)
</script>

<template>
  <main class="hire-page job-candidates-page">
    <header class="hire-heading job-candidates-heading">
      <div><h1>{{ data?.job.title || '岗位候选人' }}</h1><p v-if="data">{{ data.job.department_name }} · 关联候选人</p></div>
      <div class="job-heading-actions">
        <ElTag v-if="data" :type="data.job.status==='open'?'success':data.job.status==='paused'?'warning':'info'" effect="light">{{ jobStatusLabel(data.job.status) }}</ElTag>
        <ElTooltip content="返回岗位管理" placement="bottom"><ElButton circle aria-label="返回岗位管理" @click="router.push({name:'jobs'})"><ArrowBackOutline aria-hidden="true"/></ElButton></ElTooltip>
      </div>
    </header>
    <p v-if="error" class="hire-error" role="alert">{{ error }} <ElButton text type="primary" @click="load">重试</ElButton></p>
    <p v-else-if="!data" class="hire-muted" role="status">正在加载候选人…</p>
    <template v-if="data">
      <section class="hire-surface candidate-job-summary" aria-label="岗位要求概览">
        <div><span>招聘期限</span><strong>{{ data.job.recruitment_start || '未设置' }} 至 {{ data.job.recruitment_end || '未设置' }}</strong></div>
        <div><span>必备技能</span><div class="job-tags"><ElTag v-for="tag in data.job.required_skills" :key="tag" effect="plain" size="small">{{ tag }}</ElTag><strong v-if="!data.job.required_skills.length">未设置</strong></div></div>
        <div><span>工作经验要求</span><div class="job-tags"><ElTag v-for="tag in data.job.experience_tags" :key="tag" effect="plain" size="small">{{ tag }}</ElTag><strong v-if="!data.job.experience_tags.length">未设置</strong></div></div>
      </section>
      <section class="candidate-workbench" :aria-busy="loading">
        <section class="hire-surface candidate-job-list">
          <header class="candidates-title">
            <div><h2>关联简历 <ElTag v-if="demo" size="small" type="warning" effect="plain">演示数据</ElTag> <span>{{ data.total }} 份</span></h2><p v-if="data.matched_requirements">根据已识别的 {{ data.matched_requirements }} 项岗位要求排序；条形长度表示当前的证据命中程度。</p><p v-else>当前岗位暂未识别可核对的技能要求，按投递时间展示。</p></div>
            <div class="candidate-list-actions"><ElTooltip content="AI 分析" placement="bottom"><ElButton circle aria-label="AI 分析" @click="router.push({name:'analysis',query:{job:data.job.id}})"><SparklesOutline aria-hidden="true"/></ElButton></ElTooltip><ElTooltip content="打开候选人工作台" placement="bottom"><ElButton circle aria-label="打开候选人工作台" @click="selected && openProfile(selected.id)"><DocumentTextOutline aria-hidden="true"/></ElButton></ElTooltip></div>
          </header>
          <div class="candidate-grid-head" aria-hidden="true"><span>候选人</span><span>学历 / 经验</span><span>当前 / 最近公司</span><span>匹配证据</span><span>最近跟进</span></div>
          <div v-if="data.items.length" class="candidate-rows" role="list" aria-label="关联候选人列表">
            <article v-for="(row,index) in data.items" :key="row.id" class="candidate-row" :class="{selected:row.id===selected?.id}" role="listitem" tabindex="0" @click="selectCandidate(row)" @keydown.enter="selectCandidate(row)">
              <div class="candidate-person"><span class="candidate-order">{{ (page-1)*size+index+1 }}</span><div><button type="button" class="candidate-name" @click.stop="openProfile(row.id)">{{ row.name || '申请 #'+row.id }}</button><small>申请 #{{ row.id }} · {{ row.target_role || row.job_title }}</small></div></div>
              <div class="candidate-education"><strong>{{ degree(row.tags) }}</strong><div v-if="schoolTags(row.tags).length" class="school-tier-tags"><ElTag v-for="tier in schoolTags(row.tags)" :key="tier" size="small" effect="plain">{{ tier }}</ElTag></div><small>{{ experience(row.tags) }}</small></div>
              <div class="candidate-company">{{ row.current_company || row.recent_company || '待核实' }}</div>
              <div class="candidate-progress"><ElProgress v-if="row.match_percent!=null" :percentage="row.match_percent" :stroke-width="8" :show-text="false" color="#32887c"/><span v-else class="hire-muted">待核对</span></div>
              <div class="candidate-followup"><span :class="{'is-empty':!row.latest_note}">{{ row.latest_note || '暂无跟进' }}</span><small>候选人工作台可查看完整记录</small></div>
            </article>
          </div>
          <p v-else class="hire-empty">{{ loading?'正在加载…':'该岗位暂未关联简历' }}</p>
          <footer class="candidate-job-pagination"><span>每页</span><ElSelect v-model="size" size="small" aria-label="每页候选人数"><ElOption v-for="option in [10,20,30,50]" :key="option" :label="option+' 条'" :value="option"/></ElSelect><ElPagination v-model:current-page="page" :page-size="size" :total="data.total" :pager-count="5" layout="total, prev, pager, next"/></footer>
        </section>
        <aside class="hire-surface explain-report" aria-label="可解释匹配报告">
          <header class="report-title"><div><span class="report-eyebrow">可解释匹配报告</span><h2>{{ profileName }}</h2></div><ElTag size="small" type="info" effect="plain">演示数据</ElTag></header>
          <p class="report-disclaimer">仅展示报告版式；评分模型和证据校准尚未接入。</p>
          <div class="match-chart-wrap"><svg viewBox="0 0 240 240" class="match-chart" role="img" aria-label="四维匹配度与最终匹配度示意图"><path v-for="dimension in chartDimensions" :key="dimension.key+'-base'" :class="{dimmed:hoverPenalty && hoverPenalty!==dimension.key}" :d="arc(83,dimension.start+1,dimension.end-1)" fill="none" :stroke="dimension.pale" stroke-width="20"/><path v-for="dimension in chartDimensions" :key="dimension.key+'-fill'" :class="{dimmed:hoverPenalty && hoverPenalty!==dimension.key}" :d="arc(83,dimension.start+1,Math.max(dimension.start+1.01,dimension.fillEnd-1))" fill="none" :stroke="dimension.color" stroke-width="20"/><path :d="arc(104,0,360*penaltyLoss)" fill="none" stroke="#C0504D" stroke-width="3.5" stroke-linecap="round"/><circle cx="120" cy="120" r="56" fill="white" stroke="#eee" stroke-width="10"/><path :d="arc(56,0,360*finalScore/100)" fill="none" :stroke="finalColor" stroke-width="10" stroke-linecap="round"/><g v-for="dimension in chartDimensions" :key="dimension.key+'-label'" class="chart-label"><text :x="dimension.labelPosition.x" :y="dimension.labelPosition.y-4" text-anchor="middle">{{ dimension.label }}</text><text :x="dimension.labelPosition.x" :y="dimension.labelPosition.y+11" text-anchor="middle" class="chart-score">{{ dimension.score }}</text></g><text x="120" y="112" class="chart-final" text-anchor="middle" :fill="finalColor">{{ finalScore }}</text><text x="120" y="129" class="chart-base" text-anchor="middle">基础分 {{ baseScore }}</text><text x="120" y="142" class="chart-penalty" text-anchor="middle">扣分 −{{ penaltyLossAmount }}（×{{ penaltyFactor }}）</text></svg></div>
          <section class="dimension-list" aria-label="分维度证据"><article v-for="dimension in dimensions" :key="dimension.key" class="dimension-row" :class="{linked:hoverPenalty===dimension.key}" :style="{'--dimension-color':dimension.color,'--dimension-pale':dimension.pale}"><span class="dimension-dot"></span><div><strong>{{ dimension.label }} {{ dimension.score }}</strong><small>权重 {{ Math.round(dimension.weight*100) }}% · {{ dimension.evidence.join('、') }}</small></div></article></section>
          <section class="penalty-list" aria-label="硬性条件核对"><h3>硬性条件核对</h3><p v-for="item in penalties" :key="item.label" @mouseenter="hoverPenalty=item.linked" @mouseleave="hoverPenalty=undefined">× {{ item.label }}：{{ item.detail }} <b>{{ item.severity }} ×{{ item.factor }}</b></p></section>
        </aside>
      </section>
    </template>
  </main>
</template>

<style scoped>
.job-candidates-heading{margin-bottom:9px}.job-heading-actions,.candidate-list-actions{display:flex;align-items:center;gap:8px}.job-heading-actions :deep(.el-button),.candidate-list-actions :deep(.el-button){width:31px;height:31px;padding:0}.job-heading-actions :deep(svg),.candidate-list-actions :deep(svg){width:16px;height:16px}.candidate-job-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;padding:15px 17px;margin-bottom:14px}.candidate-job-summary>div{display:grid;gap:6px;align-content:start}.candidate-job-summary span{font-size:12px;color:var(--hire-muted)}.candidate-job-summary strong{font-size:13px}.job-tags{display:flex;gap:5px;flex-wrap:wrap}.candidate-workbench{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,375px);gap:14px;align-items:start}.candidate-job-list{overflow:hidden}.candidates-title{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:14px 17px;border-bottom:1px solid var(--hire-line)}.candidates-title h2{font-size:16px}.candidates-title h2 span:not(.el-tag),.candidates-title p{color:var(--hire-muted);font-size:12px;font-weight:400}.candidates-title p{margin-top:5px;line-height:1.45}.candidate-grid-head,.candidate-row{display:grid;grid-template-columns:minmax(142px,1.12fr) minmax(124px,.95fr) minmax(110px,.84fr) minmax(106px,.85fr) minmax(135px,1.05fr);gap:13px;align-items:center}.candidate-grid-head{padding:9px 17px;background:#f7f9fa;color:var(--hire-muted);font-size:12px}.candidate-row{width:100%;padding:12px 17px;border:0;border-bottom:1px solid #edf1f3;background:white;text-align:left;cursor:pointer;transition:background-color .14s ease,box-shadow .14s ease}.candidate-row:hover,.candidate-row:focus-visible{background:#f6fbfa;outline:none}.candidate-row.selected{background:#eff8f6;box-shadow:inset 3px 0 var(--hire-accent)}.candidate-person{display:flex;gap:9px;align-items:flex-start;min-width:0}.candidate-order{width:19px;height:19px;display:grid;place-items:center;flex:none;border-radius:50%;background:#eef3f3;color:#597170;font-size:11px;font-variant-numeric:tabular-nums;margin-top:1px}.candidate-name{border:0;padding:0;background:none;color:var(--hire-ink);font:650 13px/1.35 inherit;cursor:pointer;text-decoration:none}.candidate-name:hover{color:var(--hire-accent-dark)}.candidate-person small,.candidate-education small,.candidate-followup small{display:block;color:var(--hire-muted);font-size:11px;margin-top:3px;line-height:1.3}.candidate-education{min-width:0}.candidate-education strong{font-size:13px}.school-tier-tags{display:flex;gap:3px;flex-wrap:wrap;margin-top:3px}.school-tier-tags :deep(.el-tag){height:18px;padding:0 4px;border-radius:3px;font-size:10px}.candidate-company,.candidate-followup{font-size:12px;line-height:1.45;overflow-wrap:anywhere}.candidate-progress{min-width:0}.candidate-progress :deep(.el-progress-bar__outer){background:#e7efed}.candidate-followup .is-empty{color:var(--hire-muted)}.candidate-job-pagination{display:flex;align-items:center;justify-content:flex-end;gap:7px;padding:11px 15px;font-size:12px;color:var(--hire-muted)}.candidate-job-pagination :deep(.el-select){width:76px}.explain-report{padding:15px;position:sticky;top:12px}.report-title{display:flex;align-items:start;justify-content:space-between;gap:8px}.report-eyebrow{display:block;color:var(--hire-accent-dark);font-size:11px;font-weight:650;letter-spacing:.06em}.report-title h2{font-size:16px;margin-top:2px}.report-disclaimer{margin-top:6px;color:var(--hire-muted);font-size:11px;line-height:1.45}.match-chart-wrap{display:grid;place-items:center;margin:2px auto -2px}.match-chart{width:min(100%,248px);overflow:visible}.match-chart path{transition:opacity .15s ease}.match-chart path.dimmed{opacity:.3}.chart-label{font-size:10px;fill:#465767;font-weight:650}.chart-score{font-size:12px;fill:#202c3b}.chart-final{font-size:25px;font-weight:720}.chart-base{font-size:10px;fill:#677585}.chart-penalty{font-size:9px;fill:#C0504D;font-weight:650}.dimension-list{display:grid;gap:5px;margin-top:10px}.dimension-row{display:flex;gap:8px;align-items:flex-start;padding:7px 8px;border:1px solid #edf0f2;border-radius:7px;transition:border-color .15s ease,background-color .15s ease}.dimension-row.linked{border-color:var(--dimension-color);background:var(--dimension-pale)}.dimension-dot{display:block;width:7px;height:7px;flex:none;border-radius:50%;background:var(--dimension-color);margin-top:5px}.dimension-row strong{display:block;font-size:12px}.dimension-row small{display:block;margin-top:2px;font-size:11px;line-height:1.35;color:var(--hire-muted)}.penalty-list{border-top:1px solid var(--hire-line);margin-top:12px;padding-top:10px}.penalty-list h3{font-size:12px;margin-bottom:5px}.penalty-list p{color:#885450;font-size:11px;line-height:1.55;cursor:default}.penalty-list b{color:#bd534f;font-weight:650}@media(max-width:1120px){.candidate-workbench{grid-template-columns:1fr}.explain-report{position:static;display:grid;grid-template-columns:minmax(220px,.9fr) minmax(260px,1.1fr);column-gap:14px}.report-title,.report-disclaimer{grid-column:1/-1}.match-chart-wrap{grid-column:1}.dimension-list{grid-column:2;grid-row:3/4;align-self:center}.penalty-list{grid-column:1/-1}}@media(max-width:760px){.candidate-job-summary{grid-template-columns:1fr;gap:11px}.candidate-grid-head{display:none}.candidate-row{grid-template-columns:1fr 1fr;gap:10px}.candidate-progress{grid-column:1/-1;grid-row:3}.candidate-followup{grid-column:1/-1;grid-row:4;border-top:1px dashed #dce5e4;padding-top:7px}.candidate-job-pagination{justify-content:flex-start;flex-wrap:wrap}.explain-report{display:block}.dimension-list{margin-top:10px}.candidate-company{display:none}}
</style>

from __future__ import annotations

from jobbot.adapters.boss import (
    detect_guard,
    detect_login_state,
    load_selectors,
    parse_job_cards,
    parse_job_detail,
)
from jobbot.models import Job

SEARCH_HTML = """
<html><body>
<ul class="job-list">
  <li class="job-card-wrapper">
    <a class="job-card-left" href="/job_detail/abc123.html?lid=1">
      <div class="job-name">Java后端开发工程师</div>
      <div class="company-name">大连示例科技有限公司</div>
      <div class="salary">15-25K·13薪</div>
      <div class="job-area">大连·高新园区</div>
      <ul class="tag-list"><li>Java</li><li>Spring Boot</li><li>3-5年</li></ul>
    </a>
    <div class="info-public">王女士</div>
  </li>
  <li class="job-card-wrapper">
    <a class="job-card-left" href="/job_detail/def456.html">
      <div class="job-name">销售代表</div>
      <div class="company-name">某商贸公司</div>
      <div class="salary">8-12K</div>
      <div class="job-area">大连</div>
      <ul class="tag-list"><li>销售</li></ul>
    </a>
    <span>已沟通</span>
  </li>
  <li class="job-card-wrapper">
    <a class="job-card-left" href="/job_detail/abc123.html">
      <div class="job-name">重复卡片</div>
    </a>
  </li>
  <li class="job-card-wrapper"><div class="job-name">没有链接</div></li>
</ul>
</body></html>
"""

DETAIL_HTML = """
<html><body>
  <div class="company-info"><span class="company-name">大连示例科技有限公司</span></div>
  <div class="job-banner">
    <span class="salary">16-26K</span>
    <span class="text-city">大连·高新园区</span>
  </div>
  <div class="job-sec-text">负责 Java 后端开发，使用 Spring Boot 与 PostgreSQL，
    参与微服务架构设计与数据库性能优化。</div>
  <ul class="job-keyword-list"><li>Java</li><li>PostgreSQL</li></ul>
  <div class="job-boss-info"><span class="name">李经理</span></div>
  <div>继续沟通</div>
</body></html>
"""


def selectors(root):
    return load_selectors(root / "config" / "selectors.boss.yaml")


def test_parse_job_cards(root):
    jobs = parse_job_cards(SEARCH_HTML, "boss", selectors(root))
    assert [job.job_id for job in jobs] == ["abc123", "def456"]
    first = jobs[0]
    assert first.title == "Java后端开发工程师"
    assert first.company == "大连示例科技有限公司"
    assert first.salary == "15-25K·13薪"
    assert first.location == "大连·高新园区"
    assert first.url == "https://www.zhipin.com/job_detail/abc123.html?lid=1"
    assert first.tags == ["Java", "Spring Boot", "3-5年"]
    assert first.recruiter == "王女士"
    assert first.raw["already_chat"] is False
    assert jobs[1].raw["already_chat"] is True


def test_parse_job_detail(root):
    job = Job(
        platform="boss",
        job_id="abc123",
        title="Java后端开发工程师",
        company="旧公司",
        salary="15-25K",
        tags=["Java", "3-5年"],
        url="https://www.zhipin.com/job_detail/abc123.html",
    )
    updated = parse_job_detail(DETAIL_HTML, job, selectors(root))
    assert "PostgreSQL" in updated.description
    assert updated.company == "大连示例科技有限公司"
    assert updated.salary == "16-26K"
    assert updated.location == "大连·高新园区"
    assert updated.recruiter == "李经理"
    assert updated.tags[:2] == ["Java", "3-5年"]
    assert "PostgreSQL" in updated.tags
    assert updated.raw["already_chat"] is True


def test_detect_guard(root):
    sel = selectors(root)
    assert detect_guard("<html><body>正常页面</body></html>", "https://www.zhipin.com/", sel) is None
    assert detect_guard("<div class='geetest_panel'></div>", "https://x/", sel) == "captcha"
    assert detect_guard("<html><body>请完成验证</body></html>", "https://x/", sel) == "captcha"
    assert detect_guard("<div class='risk-tip'>异常</div>", "https://x/", sel) == "risk"
    assert detect_guard("<div class='login-dialog'></div>", "https://x/", sel) == "login"
    assert detect_guard("", "https://www.zhipin.com/web/user/?ka=header-login", sel) == "login"
    assert (
        detect_guard(
            "",
            "https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl=x",
            sel,
        )
        == "captcha"
    )


def test_detect_login_state(root):
    sel = selectors(root)
    clean = "<html><body>职位列表</body></html>"
    assert detect_login_state(clean, "https://www.zhipin.com/", {"zp_token"}, sel) is True
    assert detect_login_state(clean, "https://www.zhipin.com/", set(), sel) is False
    assert (
        detect_login_state("<html><body>我的简历</body></html>", "https://www.zhipin.com/", set(), sel)
        is True
    )
    assert (
        detect_login_state("<html><body>登录/注册</body></html>", "https://www.zhipin.com/", set(), sel)
        is False
    )
    assert (
        detect_login_state("<html><body>安全验证</body></html>", "https://www.zhipin.com/", {"zp_token"}, sel)
        is False
    )

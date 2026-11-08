import styles from "./new-order.module.css";

export function CustomsFilingGuide() {
  return (
    <details className={`${styles.panel} ${styles.customsGuide}`}>
      <summary>海关备案自助指南</summary>
      <p>适用于已取得中国境内市场主体资格、计划以自己名义出口、尚未办理进出口货物收发货人备案的商家。可边准备备案边整理上架资料。</p>
      <ol>
        <li>
          <strong>核对主体并备齐资料。</strong>准备营业执照、统一社会信用代码及企业公章。若注册时已通过“多证合一”同步申请，先到单一窗口查询备案结果。
        </li>
        <li>
          <strong>进入备案申请。</strong>用企业账号登录中国国际贸易单一窗口标准版，进入“企业资质”，选择进出口货物收发货人备案。也可通过“互联网＋海关”搜索同名事项。
        </li>
        <li>
          <strong>填写并提交。</strong>按营业执照核对企业名称、统一社会信用代码和注册地址，按注册地址选择注册关区。按页面提示填写《报关单位备案信息表》。使用账号登录办理时，按要求上传加盖公章的表格扫描件或照片，核对后提交。
        </li>
        <li>
          <strong>查询结果并保存回执。</strong>回到提交平台查看办理结果。若收到退回或补正通知，按反馈修改后重新提交。审核通过后，可在单一窗口查询并打印备案回执。
        </li>
      </ol>
      <p>材料或办理入口有疑问，可咨询所在地海关或拨打 12360。</p>
      <div className="account-actions">
        <a className="button button-outline" href="https://www.singlewindow.cn/" target="_blank" rel="noopener noreferrer">打开中国国际贸易单一窗口 ↗</a>
        <a className="button button-outline" href="https://online.customs.gov.cn/static/pages/guides/000729011000/000729011000.html" target="_blank" rel="noopener noreferrer">查看海关官方办事指南 ↗</a>
      </div>
    </details>
  );
}

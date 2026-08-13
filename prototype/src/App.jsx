import {
  App as AntApp,
  Alert,
  Avatar,
  Button,
  Collapse,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Flex,
  Grid,
  Input,
  Layout,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CodeOutlined,
  CommentOutlined,
  FullscreenOutlined,
  InfoCircleOutlined,
  MenuOutlined,
  MinusOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const { Header, Content, Footer, Sider } = Layout;
const { Text, Title, Paragraph } = Typography;
const layerTitles = {
  requirement: "需求",
  design: "技术设计",
  implementation: "代码实现",
  verification: "测试证据",
};
const runStatusTitles = {
  PENDING: "等待开发",
  RUNNING: "正在开发",
  VERIFYING: "正在验证",
  MERGING: "正在合并",
  COMPLETED: "已完成",
  NEEDS_INPUT: "需要补充信息",
  FAILED: "失败",
};
const sourceTitles = {
  DECLARED: "已声明需求",
  DERIVED: "静态推导",
  OBSERVED: "运行观察",
  INFERRED: "AI 推断",
  UNRESOLVED: "待确认",
};

async function request(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败");
  return body;
}

function Conversation({ state, draft, setDraft, sending, onSubmit, inputRef }) {
  const messages = state?.messages || [];
  const modeling = state?.requirement?.operationStatus === "MODELING";
  return (
    <Flex vertical className="panel conversation-panel">
      <div className="panel-heading"><Title level={4}>需求对话</Title></div>
      <div className="message-list">
        {messages.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="描述第一个需求" />
        ) : messages.map((item) => {
          const user = item.role === "USER";
          return (
            <div className="message-item" key={item.id}>
              <Avatar className={user ? "user-avatar" : "ai-avatar"}>{user ? "你" : "AI"}</Avatar>
              <div className="message-body">
                <Flex justify="space-between" gap={8}>
                  <Text strong>{user ? "你" : "AI"}</Text>
                  <Text type="secondary" className="message-time">
                    {new Date(item.createdAt).toLocaleString("zh-CN", {
                      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                    })}
                  </Text>
                </Flex>
                <Paragraph type="secondary">{item.content}</Paragraph>
              </div>
            </div>
          );
        })}
      </div>
      {modeling && <Spin size="small" description="AI 正在更新统一图…"><div className="thinking-space" /></Spin>}
      <div className="composer">
        <Input.TextArea
          ref={inputRef}
          value={draft}
          maxLength={4000}
          autoSize={{ minRows: 4, maxRows: 8 }}
          placeholder="继续描述或修改需求…"
          onChange={(event) => setDraft(event.target.value)}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        <Flex justify="space-between" align="center">
          <Text type="secondary" className="hint">Enter 发送，Shift+Enter 换行</Text>
          <Button
            type="primary"
            icon={<SendOutlined />}
            aria-label="发送需求"
            disabled={!draft.trim() || modeling}
            loading={sending}
            onClick={onSubmit}
          />
        </Flex>
      </div>
    </Flex>
  );
}

function Inspector({ node, graph, changedNodeIds, context, contextLoading, codeOpen, setCodeOpen }) {
  if (!node) return <Empty description="选择图中节点查看详情" />;
  const edges = graph?.edges?.filter(
    (edge) => edge.sourceId === node.id || edge.targetId === node.id,
  ) || [];
  const details = Object.entries(node.details || {}).map(([key, value]) => ({
    key,
    label: { given: "前提", when: "操作", then: "预期" }[key] || key,
    children: Array.isArray(value) ? (
      <ul>{value.map((item) => <li key={item}>{item}</li>)}</ul>
    ) : typeof value === "object" ? JSON.stringify(value) : String(value),
  }));
  const isRequirement = node.layer === "requirement";
  const codePath = context?.codePath || [];
  const scenarioCount = context?.scenarios?.length || (node.kind === "Scenario" ? 1 : 0);
  const codeChain = codePath.length ? (
    <div className="code-chain">
      {codePath.map((item, index) => (
        <div className="code-step" key={item.id} style={{ marginInlineStart: Math.min(item.depth || 0, 4) * 10 }}>
          <div className="code-step-number">{index + 1}</div>
          <div className="code-step-content">
            <Text strong className="code-name">{item.qualifiedName || item.title}</Text>
            <Flex gap={6} wrap align="center">
              {item.location && <Text type="secondary" className="code-location">{item.location.path}:{item.location.line}</Text>}
              <Text type="secondary" className="code-summary">· {item.summary}</Text>
            </Flex>
            {item.caller && <Text type="secondary" className="code-caller">调用自 {item.caller}</Text>}
            <Space size={4} wrap>
              <Tag color={item.source === "DERIVED" ? "purple" : "default"}>{sourceTitles[item.source] || item.source}</Tag>
              <Tag color={item.verified ? "success" : "default"}>{item.verified ? "测试覆盖" : "未关联测试"}</Tag>
            </Space>
          </div>
        </div>
      ))}
      {context?.codeSnapshot && (
        <Text type="secondary" className="snapshot-note">
          代码快照 {context.codeSnapshot.commitSha.slice(0, 12)} · {context.codeSnapshot.extractors.map((item) => item.id).join("、")}
        </Text>
      )}
    </div>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该需求尚无可确认的代码调用链" />;
  return (
    <div className="panel inspector-panel">
      <Flex justify="space-between" align="center">
        <Title level={4}>{isRequirement ? "需求概要" : "节点概要"}</Title>
      </Flex>
      <Title level={5} className="inspector-title">{node.title}</Title>
      <Space size={[4, 6]} wrap>
        <Tag color={node.layer === "design" ? "purple" : "blue"}>{layerTitles[node.layer]}</Tag>
        <Tag color={node.source === "UNRESOLVED" ? "error" : "default"}>{sourceTitles[node.source] || node.source}</Tag>
        {changedNodeIds.includes(node.id) && <Tag color="success">本次变化</Tag>}
      </Space>
      <Divider />
      <Paragraph>{node.summary}</Paragraph>
      <Title level={5}>影响</Title>
      <Space separator={<Divider orientation="vertical" />}>
        <Text><Text strong>{codePath.length}</Text> 个函数</Text>
        <Text><Text strong>{scenarioCount}</Text> 个场景</Text>
        <Text><Text strong>{edges.length}</Text> 条关系</Text>
      </Space>
      {isRequirement && (
        <>
          <Divider />
          <Collapse
            className="code-collapse"
            bordered={false}
            activeKey={codeOpen ? ["code"] : []}
            onChange={(keys) => setCodeOpen(keys.includes("code"))}
            items={[{
              key: "code",
              label: <Space><CodeOutlined />代码调用链</Space>,
              extra: contextLoading ? <Spin size="small" /> : <Text type="secondary">{codePath.length} 个函数</Text>,
              children: codeChain,
            }]}
          />
        </>
      )}
      {!isRequirement && details.length > 0 && (
        <><Title level={5}>详细信息</Title><Descriptions column={1} size="small" items={details} /></>
      )}
      <Divider />
      <Text type="secondary" copyable>{node.id}</Text>
    </div>
  );
}

export function ReviewApp() {
  const { message: toast, modal } = AntApp.useApp();
  const breakpoints = Grid.useBreakpoint();
  const desktop = Boolean(breakpoints.xl);
  const wideDesktop = Boolean(breakpoints.xxl);
  const [state, setState] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [viewMode, setViewMode] = useState("changes");
  const [layer, setLayer] = useState("requirement");
  const [conversationOpen, setConversationOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [svg, setSvg] = useState("");
  const [graphError, setGraphError] = useState("");
  const [requirementContext, setRequirementContext] = useState(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [codeOpen, setCodeOpen] = useState(false);
  const [viewport, setViewport] = useState({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef(null);
  const inputRef = useRef(null);
  const graphRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      setState(await request("/api/state"));
    } catch (error) {
      toast.error(error.message);
    }
  }, [toast]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const document = state?.revision;
  const revision = document?.revision;
  const graph = document?.graph;
  const nodes = graph?.nodes || [];
  const selected = useMemo(
    () => nodes.find((node) => node.id === selectedId),
    [nodes, selectedId],
  );

  useEffect(() => {
    if (selectedId && !nodes.some((node) => node.id === selectedId)) setSelectedId(null);
  }, [nodes, selectedId]);

  useEffect(() => {
    if (!selected || selected.layer !== "requirement") {
      setRequirementContext(null);
      return;
    }
    let active = true;
    setContextLoading(true);
    request(`/api/requirement/${encodeURIComponent(selected.id)}/context`)
      .then((value) => { if (active) setRequirementContext(value); })
      .catch((error) => { if (active) toast.error(error.message); })
      .finally(() => { if (active) setContextLoading(false); });
    return () => { active = false; };
  }, [selected?.id, selected?.layer, revision?.id, toast]);

  useEffect(() => setCodeOpen(false), [selected?.id]);

  useEffect(() => {
    if (!revision) return;
    const parameters = new URLSearchParams({ layers: layer });
    if (selectedId) parameters.set("focusId", selectedId);
    setGraphError("");
    fetch(`/api/graph.svg?${parameters}`)
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json();
          throw new Error(body.error || "行为图生成失败");
        }
        return response.text();
      })
      .then(setSvg)
      .catch((error) => setGraphError(error.message));
  }, [layer, revision?.id, selectedId]);

  useEffect(() => {
    graphRef.current?.querySelectorAll("g.node").forEach((element) => {
      element.setAttribute("role", "button");
      element.setAttribute("tabindex", "0");
      element.setAttribute("aria-label", element.querySelector("title")?.textContent || element.id);
    });
  }, [svg]);

  async function submit() {
    const content = draft.trim();
    if (!content || sending) return;
    setSending(true);
    try {
      await request("/api/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      setDraft("");
      setConversationOpen(false);
      await refresh();
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSending(false);
    }
  }

  function activateNode(event) {
    if (event.type === "keydown" && !["Enter", " "].includes(event.key)) return;
    const node = event.target.closest?.("g.node");
    if (!node?.id) return;
    event.preventDefault();
    setSelectedId(node.id);
    setViewport((current) => ({ ...current, x: 0, y: 0 }));
    if (!desktop) setInspectorOpen(true);
  }

  function zoomBy(factor) {
    setViewport((current) => ({ ...current, scale: Math.min(2.5, Math.max(0.35, current.scale * factor)) }));
  }

  function beginPan(event) {
    if (event.button !== 0 || event.target.closest?.("g.node")) return;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function movePan(event) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    dragRef.current = { ...drag, x: event.clientX, y: event.clientY };
    setViewport((current) => ({ ...current, x: current.x + deltaX, y: current.y + deltaY }));
  }

  function endPan(event) {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
  }

  function discuss() {
    setConversationOpen(true);
    window.setTimeout(() => inputRef.current?.focus(), 100);
  }

  function approve() {
    modal.confirm({
      title: "批准并自动交付当前需求？",
      icon: <InfoCircleOutlined />,
      content: `将冻结 ${revision.id}，随后自动开发、验证并安全合并当前本地分支。`,
      okText: "确认批准并自动交付",
      cancelText: "取消",
      async onOk() {
        try {
          await request(`/api/revision/${revision.id}/approve-and-start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ contentHash: revision.contentHash }),
          });
          toast.success("已批准，正在自动开发和交付");
          await refresh();
        } catch (error) {
          toast.error(error.message);
          throw error;
        }
      },
    });
  }

  if (!state) return <Spin fullscreen description="正在读取统一图…" />;

  const approvalErrors = state.approvalErrors || [];
  const canApprove = revision?.status === "CANDIDATE"
    && revision.approvable
    && approvalErrors.length === 0
    && state.requirement.operationStatus === "IDLE";
  const unavailable = Object.entries(state.dependencies || {})
    .filter(([, value]) => String(value).startsWith("不可用"));
  const conversation = <Conversation state={state} draft={draft} setDraft={setDraft} sending={sending} onSubmit={submit} inputRef={inputRef} />;
  const inspector = <Inspector node={selected} graph={graph} changedNodeIds={state.changedNodeIds || []} context={requirementContext} contextLoading={contextLoading} codeOpen={codeOpen} setCodeOpen={setCodeOpen} />;

  return (
    <Layout className="review-shell">
      {desktop && <Sider width={wideDesktop ? 326 : 286} theme="light" className="side-panel">{conversation}</Sider>}
      <Layout className="main-column">
        <Header className="workspace-header">
          <Flex justify="space-between" align="flex-start" gap={12} wrap>
            <div>
              <Title level={3}>统一图变更</Title>
              <Space wrap>
                <Text>{revision.id}</Text>
                <Tag color={revision.status === "CANDIDATE" ? "processing" : "success"}>
                  {revision.status === "CANDIDATE" ? "候选" : "已批准"}
                </Tag>
                {approvalErrors.length === 0 ? (
                  <Text type="success"><CheckCircleOutlined /> 无待确认问题</Text>
                ) : <Text type="danger">{approvalErrors.length} 个批准阻断</Text>}
              </Space>
            </div>
            <Space>
              <Button icon={<CommentOutlined />} onClick={() => setConversationOpen(true)}>对话</Button>
              {!desktop && <Button icon={<MenuOutlined />} disabled={!selected} onClick={() => setInspectorOpen(true)}>详情</Button>}
            </Space>
          </Flex>
          <Flex justify="space-between" gap={12} wrap className="graph-controls">
            <Segmented value={viewMode} onChange={setViewMode} options={[
              { label: "仅看变化", value: "changes" }, { label: "显示上下文", value: "context" },
            ]} />
            <Segmented value={layer} onChange={setLayer} options={[
              { label: "需求", value: "requirement" },
              { label: "技术设计", value: "design" },
              { label: "代码实现", value: "implementation" },
              { label: "测试证据", value: "verification" },
            ]} />
          </Flex>
        </Header>
        <Content className="graph-content">
          {unavailable.length > 0 && <Alert showIcon type="warning" message={`本地依赖不可用：${unavailable.map(([name]) => name).join("、")}`} />}
          {graphError ? (
            <Alert type="error" showIcon message="行为图生成失败" description={graphError} />
          ) : svg ? (
            <div
              ref={graphRef}
              className={`graph-svg ${viewMode === "changes" ? "only-changes" : ""} ${selectedId ? "has-focus" : ""}`}
              onClick={activateNode}
              onKeyDown={activateNode}
              onPointerDown={beginPan}
              onPointerMove={movePan}
              onPointerUp={endPan}
              onPointerCancel={endPan}
            >
              <Space.Compact className="canvas-tools">
                <Button aria-label="适应画布" icon={<FullscreenOutlined />} onClick={() => setViewport({ scale: 1, x: 0, y: 0 })} />
                <Button aria-label="缩小统一图" icon={<MinusOutlined />} onClick={() => zoomBy(0.8)} />
                <Button aria-label="复位统一图视图" onClick={() => setViewport({ scale: 1, x: 0, y: 0 })}>{Math.round(viewport.scale * 100)}%</Button>
                <Button aria-label="放大统一图" icon={<PlusOutlined />} onClick={() => zoomBy(1.25)} />
                <Button aria-label="重新生成统一图" icon={<ReloadOutlined />} onClick={() => setViewport({ scale: 1, x: 0, y: 0 })} />
              </Space.Compact>
              <div
                className="graph-transform"
                style={{ transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})` }}
                dangerouslySetInnerHTML={{ __html: svg }}
              />
            </div>
          ) : <Spin description="正在生成行为图…"><div className="graph-loading" /></Spin>}
        </Content>
        <Footer className="approval-bar">
          <Flex justify="space-between" align="center" gap={16} wrap>
            <Space orientation="vertical" size={2}>
              <Text>层级计数：需求 {state.layerCounts.requirement} · 技术设计 {state.layerCounts.design} · 代码实现 {state.layerCounts.implementation} · 测试证据 {state.layerCounts.verification}</Text>
              {state.implementationRun && <Text type="secondary" aria-live="polite">自动交付 {runStatusTitles[state.implementationRun.status] || state.implementationRun.status}：{state.implementationRun.summary || "等待执行"}</Text>}
            </Space>
            <Space wrap>
              <Button type="text" onClick={discuss}>继续讨论</Button>
              <Button type="primary" size="large" disabled={!canApprove} onClick={approve}>批准并自动交付</Button>
            </Space>
          </Flex>
        </Footer>
      </Layout>
      {desktop && <Sider width={wideDesktop ? 360 : 350} theme="light" className="side-panel">{inspector}</Sider>}
      <Drawer title="需求对话" placement="left" size="min(92vw, 420px)" open={conversationOpen} onClose={() => setConversationOpen(false)}>{conversation}</Drawer>
      <Drawer title="节点详情" placement="right" size="min(92vw, 420px)" open={inspectorOpen} onClose={() => setInspectorOpen(false)}>{inspector}</Drawer>
    </Layout>
  );
}

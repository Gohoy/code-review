import {
  App as AntApp,
  Alert,
  Avatar,
  Button,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Flex,
  Grid,
  Input,
  Layout,
  List,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CommentOutlined,
  InfoCircleOutlined,
  MenuOutlined,
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
      <List
        className="message-list"
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="描述第一个需求" /> }}
        dataSource={messages}
        renderItem={(item) => {
          const user = item.role === "USER";
          return (
            <List.Item>
              <List.Item.Meta
                avatar={<Avatar className={user ? "user-avatar" : "ai-avatar"}>{user ? "你" : "AI"}</Avatar>}
                title={
                  <Flex justify="space-between" gap={8}>
                    <Text strong>{user ? "你" : "AI"}</Text>
                    <Text type="secondary" className="message-time">
                      {new Date(item.createdAt).toLocaleString("zh-CN", {
                        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                      })}
                    </Text>
                  </Flex>
                }
                description={<Paragraph>{item.content}</Paragraph>}
              />
            </List.Item>
          );
        }}
      />
      {modeling && <Spin size="small" tip="AI 正在更新统一图…"><div className="thinking-space" /></Spin>}
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

function Inspector({ node, graph, changedNodeIds }) {
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
  return (
    <div className="panel inspector-panel">
      <Title level={4}>{node.title}</Title>
      <Space size={[4, 6]} wrap>
        <Tag color={node.layer === "design" ? "purple" : "blue"}>{layerTitles[node.layer]}</Tag>
        <Tag>{node.kind}</Tag>
        <Tag color={node.source === "UNRESOLVED" ? "error" : "default"}>{node.source}</Tag>
        {changedNodeIds.includes(node.id) && <Tag color="success">本次变化</Tag>}
      </Space>
      <Divider />
      <Title level={5}>说明</Title>
      <Paragraph>{node.summary}</Paragraph>
      <Title level={5}>影响</Title>
      <Paragraph>{edges.length} 条关联。</Paragraph>
      {details.length > 0 && (
        <><Title level={5}>详细信息</Title><Descriptions column={1} size="small" items={details} /></>
      )}
      <Text type="secondary" copyable>{node.id}</Text>
    </div>
  );
}

export function ReviewApp() {
  const { message: toast, modal } = AntApp.useApp();
  const desktop = Boolean(Grid.useBreakpoint().xl);
  const [state, setState] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [viewMode, setViewMode] = useState("changes");
  const [layer, setLayer] = useState("all");
  const [conversationOpen, setConversationOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [svg, setSvg] = useState("");
  const [graphError, setGraphError] = useState("");
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
    if (nodes.length && !nodes.some((node) => node.id === selectedId)) {
      const changed = [...(state?.changedNodeIds || [])]
        .reverse()
        .find((nodeId) => nodes.some((node) => node.id === nodeId));
      setSelectedId(changed || graph.entryNodeIds?.[0] || nodes[0].id);
    }
  }, [graph, nodes, selectedId, state?.changedNodeIds]);

  useEffect(() => {
    if (!revision) return;
    const parameters = new URLSearchParams({
      layers: layer === "all" ? "requirement,design" : layer,
    });
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
    if (!desktop) setInspectorOpen(true);
  }

  function discuss() {
    if (!desktop) setConversationOpen(true);
    window.setTimeout(() => inputRef.current?.focus(), 100);
  }

  function approve() {
    modal.confirm({
      title: "批准当前需求与技术设计？",
      icon: <InfoCircleOutlined />,
      content: `将冻结 ${revision.id}，随后在隔离 Git worktree 中启动本机 Codex。`,
      okText: "确认批准并开始开发",
      cancelText: "取消",
      async onOk() {
        try {
          await request(`/api/revision/${revision.id}/approve-and-start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ contentHash: revision.contentHash }),
          });
          toast.success("已批准，正在创建隔离 worktree");
          await refresh();
        } catch (error) {
          toast.error(error.message);
          throw error;
        }
      },
    });
  }

  if (!state) return <Spin fullscreen tip="正在读取统一图…" />;

  const approvalErrors = state.approvalErrors || [];
  const canApprove = revision?.status === "CANDIDATE"
    && revision.approvable
    && approvalErrors.length === 0
    && state.requirement.operationStatus === "IDLE";
  const unavailable = Object.entries(state.dependencies || {})
    .filter(([, value]) => String(value).startsWith("不可用"));
  const conversation = <Conversation state={state} draft={draft} setDraft={setDraft} sending={sending} onSubmit={submit} inputRef={inputRef} />;
  const inspector = <Inspector node={selected} graph={graph} changedNodeIds={state.changedNodeIds || []} />;

  return (
    <Layout className="review-shell">
      {desktop && <Sider width={320} theme="light" className="side-panel">{conversation}</Sider>}
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
            {!desktop && (
              <Space>
                <Button icon={<CommentOutlined />} onClick={() => setConversationOpen(true)}>对话</Button>
                <Button icon={<MenuOutlined />} onClick={() => setInspectorOpen(true)}>详情</Button>
              </Space>
            )}
          </Flex>
          <Flex gap={12} wrap className="graph-controls">
            <Segmented value={viewMode} onChange={setViewMode} options={[
              { label: "仅看变化", value: "changes" }, { label: "显示上下文", value: "context" },
            ]} />
            <Segmented value={layer} onChange={setLayer} options={[
              { label: "需求与设计", value: "all" }, { label: "需求", value: "requirement" },
              { label: "技术设计", value: "design" },
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
              className={`graph-svg ${viewMode === "changes" ? "only-changes" : ""}`}
              onClick={activateNode}
              onKeyDown={activateNode}
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : <Spin tip="正在生成行为图…"><div className="graph-loading" /></Spin>}
        </Content>
        <Footer className="approval-bar">
          <Flex justify="space-between" align="center" gap={16} wrap>
            <Space direction="vertical" size={2}>
              <Text>层级计数：需求 {state.layerCounts.requirement} · 技术设计 {state.layerCounts.design} · 代码实现 {state.layerCounts.implementation} · 测试证据 {state.layerCounts.verification}</Text>
              {state.implementationRun && <Text type="secondary">开发运行 {state.implementationRun.status}：{state.implementationRun.summary || "等待执行"}</Text>}
            </Space>
            <Space wrap>
              <Button type="text" onClick={discuss}>继续讨论</Button>
              <Button type="primary" size="large" disabled={!canApprove} onClick={approve}>批准并开始开发</Button>
            </Space>
          </Flex>
        </Footer>
      </Layout>
      {desktop && <Sider width={300} theme="light" className="side-panel">{inspector}</Sider>}
      <Drawer title="需求对话" placement="left" width="min(92vw, 420px)" open={conversationOpen} onClose={() => setConversationOpen(false)}>{conversation}</Drawer>
      <Drawer title="节点详情" placement="right" width="min(92vw, 420px)" open={inspectorOpen} onClose={() => setInspectorOpen(false)}>{inspector}</Drawer>
    </Layout>
  );
}

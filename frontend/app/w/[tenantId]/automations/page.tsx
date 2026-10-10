import { WorkflowsView } from "@/components/WorkflowsView";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function AutomationsPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <WorkflowsView tenantId={tenantId} />
    </>
  );
}

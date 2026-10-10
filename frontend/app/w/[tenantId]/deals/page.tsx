import { DealsBoard } from "@/components/DealsBoard";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function DealsPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <DealsBoard tenantId={tenantId} />
    </>
  );
}

import { FormsView } from "@/components/FormsView";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function FormsPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <FormsView tenantId={tenantId} />
    </>
  );
}

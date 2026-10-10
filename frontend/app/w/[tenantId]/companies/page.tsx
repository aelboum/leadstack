import { CompaniesView } from "@/components/CompaniesView";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function CompaniesPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <CompaniesView tenantId={tenantId} />
    </>
  );
}

import { ContactDetailView } from "@/components/ContactDetailView";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function ContactDetailPage({
  params,
}: {
  params: Promise<{ tenantId: string; contactId: string }>;
}) {
  const { tenantId, contactId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <ContactDetailView tenantId={tenantId} contactId={contactId} />
    </>
  );
}

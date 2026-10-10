import { ContactsView } from "@/components/ContactsView";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default async function ContactsPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <>
      <WorkspaceTabs tenantId={tenantId} />
      <ContactsView tenantId={tenantId} />
    </>
  );
}

import type { Rec } from "../app/context";
import { ApprovalRecord } from "./Approval";
import { BillRecord, ContactRecord, DestinationRecord, ExpenseRecord, NotificationsRecord } from "./Others";
import { PaymentRecord } from "./Payment";

export const TITLES: Record<Rec["kind"], string> = { approval: "Approval", payment: "Payment", bill: "Bill", contact: "Contact", dest: "Destination", expense: "Expense", notifications: "Notifications" };

export function RecordView({ rec }: { rec: Rec }) {
  const id = rec.id || "";
  switch (rec.kind) {
    case "approval": return <ApprovalRecord key={id} id={id} />;
    case "payment": return <PaymentRecord key={id} id={id} />;
    case "bill": return <BillRecord key={id} id={id} />;
    case "contact": return <ContactRecord key={id} id={id} />;
    case "dest": return <DestinationRecord key={id} id={id} />;
    case "expense": return <ExpenseRecord key={id} id={id} />;
    case "notifications": return <NotificationsRecord />;
    default: return null;
  }
}

import type { IncidentType } from "./types";
import { tr, translateCode, getLanguage } from "./i18n";
export const labels: Record<IncidentType, string> = {
  get MISSING_RECORDS() {
    return tr("Missing records");
  },
  get DUPLICATE_RECORDS() {
    return tr("Duplicate records");
  },
  get SCHEMA_DRIFT() {
    return tr("Schema drift");
  },
  get LATE_ARRIVING_DATA() {
    return tr("Late-arriving data");
  },
};
export const short = (value: string) => value.slice(0, 8);
export const human = translateCode;
export const date = (value: string | null) =>
  value && !Number.isNaN(Date.parse(value))
    ? new Intl.DateTimeFormat(
        getLanguage() === "ar" ? "ar-JO-u-nu-latn" : "en-GB",
        {
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          timeZone: "UTC",
          hour12: false,
        },
      ).format(new Date(value)) + " UTC"
    : tr("Not recorded");
